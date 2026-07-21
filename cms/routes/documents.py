import logging
import os
import uuid

import flask
from flask import request, jsonify, current_app, abort, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from . import cms_bp
from ..models import db, Case, Subject, Document, AuditLog
from ..auth import (
    roles_required,
    case_access_required,
    case_edit_required,
    subject_access_required,
    apply_tenant_filter,
    ensure_tenant_access,
)
from ..image_validation import validate_upload
from ..validation import validate, DocumentUploadSchema
from ..tier_limits import check_storage_limit, check_resource_limit

from .response import api_success, api_error

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "txt",
    "csv",
}
UPLOAD_FOLDER = "uploads"


def allowed_file(filename) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@cms_bp.route("/cases/<case_id>/upload", methods=["POST"])
@login_required
@case_access_required
@case_edit_required
@validate(DocumentUploadSchema)
def upload_case_document(case_id: str) -> flask.Response:
    """Upload a document to a case."""
    db.session.get(Case, case_id) or abort(404)

    if "file" not in request.files:
        return api_error("No file provided", 400)

    file = request.files["file"]
    filename = file.filename or ""

    if filename == "":
        return api_error("No file selected", 400)

    if not allowed_file(filename):
        return api_error("File type not allowed", 400)

    # Validate file content by magic bytes
    file_ext = filename.rsplit(".", 1)[1].lower()
    is_valid, detected = validate_upload(file, file_ext)
    if not is_valid:
        logger.warning(
            f"Upload rejected: {file.filename} (detected: {detected or 'unknown'})"
        )
        return jsonify(
            {
                "error": f"File content does not match extension ({detected or 'unknown format'})"
            }
        ), 400

    # Check storage quota before saving
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    ok, used_mb, max_mb = check_storage_limit(
        current_user.tenant_id, extra_bytes=file_size
    )
    if not ok:
        return api_error(
            f"Storage limit reached ({used_mb}/{max_mb} MB). Upgrade your plan to upload more files.",
            403,
        )

    # Check document count limit
    ok, cur, maximum = check_resource_limit(Document, "tenant_id", "max_documents")
    if not ok:
        return api_error(
            f"Document limit reached ({cur}/{maximum}). Upgrade your plan to upload more documents.",
            403,
        )

    # Create upload directory if not exists
    upload_dir = os.path.join(
        current_app.root_path, "static", UPLOAD_FOLDER, "cases", case_id
    )
    os.makedirs(upload_dir, exist_ok=True)

    # Generate unique filename
    original_filename = secure_filename(filename)
    unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
    file_path = os.path.join(upload_dir, unique_filename)

    # Save file
    file.save(file_path)

    # Get file size
    file_size = os.path.getsize(file_path)

    vd = request.validated_data

    # Create document record
    document = Document(
        case_id=case_id,
        filename=unique_filename,
        original_filename=original_filename,
        mime_type=file.content_type,
        file_size=file_size,
        storage_path=f"{UPLOAD_FOLDER}/cases/{case_id}/{unique_filename}",
        storage_type="local",
        document_type=vd.get("document_type", "evidence"),
        description=vd.get("description", ""),
        classification=vd.get("classification", "confidential"),
        uploaded_by=current_user.id,
    )

    db.session.add(document)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="document",
        entity_id=document.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Uploaded document: {original_filename}",
    )
    db.session.commit()

    return jsonify(
        {"message": "Document uploaded", "document": document.to_dict()}
    ), 201


@cms_bp.route("/subjects/<subject_id>/upload", methods=["POST"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@validate(DocumentUploadSchema)
def upload_subject_document(subject_id: str) -> flask.Response:
    """Upload a document to a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    if "file" not in request.files:
        return api_error("No file provided", 400)

    file = request.files["file"]
    filename = file.filename or ""

    if filename == "":
        return api_error("No file selected", 400)

    if not allowed_file(filename):
        return api_error("File type not allowed", 400)

    # Validate file content by magic bytes
    file_ext = filename.rsplit(".", 1)[1].lower()
    is_valid, detected = validate_upload(file, file_ext)
    if not is_valid:
        logger.warning(
            f"Upload rejected: {file.filename} (detected: {detected or 'unknown'})"
        )
        return jsonify(
            {
                "error": f"File content does not match extension ({detected or 'unknown format'})"
            }
        ), 400

    # Check storage quota before saving
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    ok, used_mb, max_mb = check_storage_limit(
        current_user.tenant_id, extra_bytes=file_size
    )
    if not ok:
        return api_error(
            f"Storage limit reached ({used_mb}/{max_mb} MB). Upgrade your plan to upload more files.",
            403,
        )

    # Check document count limit
    ok, cur, maximum = check_resource_limit(Document, "tenant_id", "max_documents")
    if not ok:
        return api_error(
            f"Document limit reached ({cur}/{maximum}). Upgrade your plan to upload more documents.",
            403,
        )

    # Create upload directory
    upload_dir = os.path.join(
        current_app.root_path, "static", UPLOAD_FOLDER, "subjects", subject_id
    )
    os.makedirs(upload_dir, exist_ok=True)

    # Generate unique filename
    original_filename = secure_filename(filename)
    file_ext = original_filename.rsplit(".", 1)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
    file_path = os.path.join(upload_dir, unique_filename)

    file.save(file_path)
    file_size = os.path.getsize(file_path)

    vd = request.validated_data

    document = Document(
        subject_id=subject_id,
        filename=unique_filename,
        original_filename=original_filename,
        mime_type=file.content_type,
        file_size=file_size,
        storage_path=f"{UPLOAD_FOLDER}/subjects/{subject_id}/{unique_filename}",
        storage_type="local",
        document_type=vd.get("document_type", "evidence"),
        description=vd.get("description", ""),
        classification=vd.get("classification", "confidential"),
        uploaded_by=current_user.id,
    )

    db.session.add(document)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="document",
        entity_id=document.id,
        ip_address=request.remote_addr,
        description=f"Uploaded document to {subject.name}: {original_filename}",
    )
    db.session.commit()

    return jsonify(
        {"message": "Document uploaded", "document": document.to_dict()}
    ), 201


@cms_bp.route("/documents/<document_id>")
@login_required
def get_document(document_id: str) -> flask.Response:
    """Get document metadata."""
    document = db.session.get(Document, document_id) or abort(404)
    ensure_tenant_access(document)

    # Check access
    if document.case_id:
        case = db.session.get(Case, document.case_id)
        ensure_tenant_access(case)
        if case and not current_user.can_access_case(case):
            return api_error("Access denied", 403)

    return jsonify(document.to_dict())


@cms_bp.route("/documents/<document_id>/download")
@login_required
def download_document(document_id: str) -> flask.Response:
    """Download a document."""
    document = db.session.get(Document, document_id) or abort(404)
    ensure_tenant_access(document)

    # Check access
    if document.case_id:
        case = db.session.get(Case, document.case_id)
        ensure_tenant_access(case)
        if case and not current_user.can_access_case(case):
            return api_error("Access denied", 403)

    if not document.storage_path:
        return api_error("Document file not found on server", 404)

    file_path = os.path.join(current_app.root_path, "static", document.storage_path)

    if not os.path.exists(file_path):
        abort(404)

    return send_from_directory(
        os.path.dirname(file_path),
        os.path.basename(file_path),
        as_attachment=True,
        download_name=document.original_filename,
    )


@cms_bp.route("/documents/<document_id>", methods=["DELETE"])
@login_required
@roles_required("admin", "owner", "senior_investigator")
def delete_document(document_id: str) -> flask.Response:
    """Delete a document."""
    document = db.session.get(Document, document_id) or abort(404)
    ensure_tenant_access(document)

    # Delete file
    file_path = os.path.join(current_app.root_path, "static", document.storage_path)
    if os.path.exists(file_path):
        os.remove(file_path)

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="document",
        entity_id=document_id,
        ip_address=request.remote_addr,
        description=f"Deleted document: {document.original_filename}",
    )

    db.session.delete(document)
    db.session.commit()

    return api_success({}, "Document deleted")


@cms_bp.route("/cases/<case_id>/documents")
@login_required
@case_access_required
def get_case_documents(case_id: str) -> flask.Response:
    """Get all documents for a case."""
    query = Document.query.filter_by(case_id=case_id, is_deleted=False)
    query = apply_tenant_filter(query, Document)
    documents = query.order_by(Document.created_at.desc()).all()

    return jsonify({"documents": [d.to_dict() for d in documents]})
