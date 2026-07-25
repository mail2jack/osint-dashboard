import logging
import os
import math

import flask
from flask import request, jsonify, abort, current_app
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Subject, AuditLog
from ..auth import roles_required, subject_access_required, apply_tenant_filter
from ..image_validation import validate_image_file
from ..validation import validate, SaveFaceEncodingSchema, CompareFacesSchema
from ..tier_limits import check_storage_limit

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/subjects/<subject_id>/photo", methods=["POST"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
def upload_subject_photo(subject_id: str) -> flask.Response:
    """Upload a photo for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    if "photo" not in request.files:
        return api_error("No photo provided", 400)

    file = request.files["photo"]

    if file.filename == "":
        return api_error("No file selected", 400)

    # Only allow images — validate by magic bytes
    is_img, detected_ext = validate_image_file(file)
    if not is_img:
        return jsonify(
            {"error": "Only image files allowed (PNG, JPEG, GIF, WebP)"}
        ), 400
    ext = detected_ext

    # Check storage quota before saving
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    ok, used_mb, max_mb = check_storage_limit(
        current_user.tenant_id, extra_bytes=file_size
    )
    if not ok:
        return api_error(
            f"Storage limit reached ({used_mb}/{max_mb} MB). Upgrade your plan to upload photos.",
            403,
        )

    # Create upload directory
    upload_dir = os.path.join(
        current_app.root_path, "static", "uploads", "subjects", subject_id
    )
    os.makedirs(upload_dir, exist_ok=True)

    # Remove old photo if exists
    if subject.photo_path:
        old_path = os.path.join(
            current_app.root_path, "static", subject.photo_path.lstrip("/")
        )
        if os.path.exists(old_path):
            os.remove(old_path)

    # Save new photo
    filename = f"photo.{ext}"
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)

    # Extract EXIF metadata
    try:
        from ..services.photo_analysis import analyze_photo

        photo_url = f"/uploads/subjects/{subject_id}/{filename}"
        analysis = analyze_photo(file_path, photo_url=photo_url)
        subject.photo_metadata = {
            "gps": analysis.get("gps"),
            "camera": analysis.get("camera"),
            "datetime": analysis.get("datetime"),
            "software": analysis.get("software"),
            "privacy": analysis.get("privacy"),
        }
    except Exception as e:
        logger.debug("EXIF extraction failed on upload for %s: %s", subject_id, e)

    # Update subject
    subject.photo_path = f"/uploads/subjects/{subject_id}/{filename}"

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="subject",
        entity_id=subject_id,
        changes={"photo": "uploaded"},
        ip_address=request.remote_addr,
        description="Uploaded subject photo",
    )
    db.session.commit()

    return jsonify({"message": "Photo uploaded", "photo_path": subject.photo_path})


@cms_bp.route("/subjects/<subject_id>/face-encoding", methods=["POST"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@validate(SaveFaceEncodingSchema)
def save_face_encoding(subject_id: str) -> flask.Response:
    """Save face encoding for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    encoding = request.validated_data.get("encoding")

    if not isinstance(encoding, list) or len(encoding) != 128:
        return api_error("Invalid encoding format", 400)

    subject.face_encoding = encoding

    AuditLog.log(
        user_id=current_user.id,
        action="face_encoding_saved",
        entity_type="subject",
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description="Saved face encoding",
    )
    db.session.commit()

    return jsonify({"message": "Face encoding saved", "has_encoding": True})


@cms_bp.route("/subjects/<subject_id>/face-encoding", methods=["DELETE"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
def delete_face_encoding(subject_id: str) -> flask.Response:
    """Delete face encoding for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    subject.face_encoding = None

    AuditLog.log(
        user_id=current_user.id,
        action="face_encoding_deleted",
        entity_type="subject",
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description="Deleted face encoding",
    )
    db.session.commit()

    return jsonify({"message": "Face encoding deleted", "has_encoding": False})


@cms_bp.route("/subjects/compare-faces", methods=["POST"])
@login_required
@validate(CompareFacesSchema)
def compare_faces() -> flask.Response:
    """Compare face encodings. Returns list of matching subjects."""
    target_encoding = request.validated_data.get("encoding", [])

    if (
        not target_encoding
        or not isinstance(target_encoding, list)
        or len(target_encoding) != 128
    ):
        return api_error("Invalid encoding format", 400)

    threshold = request.validated_data.get("threshold", 0.6)
    limit = request.validated_data.get("limit", 20)

    subjects_with_faces = apply_tenant_filter(
        Subject.query.filter(
            Subject.face_encoding.isnot(None),
            Subject.is_deleted == False,
            Subject.photo_path.isnot(None),
        ),
        Subject,
    ).all()

    def euclidean_distance(enc1, enc2):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(enc1, enc2)))

    matches = []
    for subject in subjects_with_faces:
        distance = euclidean_distance(target_encoding, subject.face_encoding)
        if distance < threshold:
            matches.append(
                {
                    "id": subject.id,
                    "name": subject.name,
                    "subject_type": subject.subject_type,
                    "photo_path": subject.photo_path,
                    "distance": round(distance, 4),
                    "similarity": round((1 - distance) * 100, 1),
                }
            )

    matches.sort(key=lambda x: x["distance"])
    matches = matches[:limit]

    return jsonify(
        {
            "matches": matches,
            "total_searched": len(subjects_with_faces),
            "threshold": threshold,
        }
    )


@cms_bp.route("/api/subjects/with-faces", methods=["GET"])
@login_required
def get_subjects_with_faces() -> flask.Response:
    """Get list of subjects with face encodings for face-api.js matching."""
    subjects = apply_tenant_filter(
        Subject.query.filter(
            Subject.face_encoding.isnot(None),
            Subject.is_deleted == False,
            Subject.photo_path.isnot(None),
        ),
        Subject,
    ).all()

    return jsonify(
        {
            "subjects": [
                {
                    "id": s.id,
                    "name": s.name,
                    "photo_path": s.photo_path,
                    "face_encoding": s.face_encoding,
                }
                for s in subjects
            ]
        }
    )
