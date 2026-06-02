import logging

import flask
from flask import request, jsonify, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import validate, AddSubjectToCaseSchema, BulkAddSubjectsSchema
from ..models import db, Case, Subject, AuditLog
from ..auth import roles_required, case_edit_required

logger = logging.getLogger(__name__)


@cms_bp.route("/cases/<case_id>/add-subject", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator", "investigator", "junior_investigator")
@case_edit_required
@validate(AddSubjectToCaseSchema)
def add_subject_to_case(case_id: str) -> flask.Response:
    """Add an existing subject to a case."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    subject_id = data.get("subject_id")
    if not subject_id:
        return jsonify({"error": "subject_id is required"}), 400

    subject = db.session.get(Subject, subject_id) or abort(404)

    if subject in case.subjects.all():
        return jsonify({"error": "Subject already linked to this case"}), 400

    case.subjects.append(subject)

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="case",
        entity_id=case_id,
        new_values={"added_subject": subject.name},
        ip_address=request.remote_addr,
        description=f"Added subject {subject.name} to case {case.case_number}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"message": "Subject added to case", "case": case.to_dict()})

    flash(f"Subject {subject.name} added to case.", "success")
    return redirect(url_for("cms.view_case", case_id=case_id))


@cms_bp.route("/cases/<case_id>/add-subjects-bulk", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator", "investigator", "junior_investigator")
@case_edit_required
@validate(BulkAddSubjectsSchema)
def bulk_add_subjects_to_case(case_id: str) -> flask.Response:
    """Add multiple subjects to a case at once."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    subject_ids = data.get("subject_ids", [])
    if not subject_ids:
        return jsonify({"error": "subject_ids required"}), 400

    if isinstance(subject_ids, str):
        subject_ids = [s.strip() for s in subject_ids.split(",")]

    # Batch-load subjects + existing links
    subjects = {
        s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()
    }
    existing_linked_ids = {s.id for s in case.subjects.all()}

    added = []
    skipped = []

    for subject_id in subject_ids:
        subject = subjects.get(subject_id)
        if not subject:
            skipped.append({"id": subject_id, "reason": "Not found"})
            continue

        if subject.id in existing_linked_ids:
            skipped.append({"name": subject.name, "reason": "Already linked"})
            continue

        case.subjects.append(subject)
        added.append(subject.name)

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="case",
            entity_id=case_id,
            new_values={"added_subject": subject.name},
            ip_address=request.remote_addr,
            description=f"Added subject {subject.name} to case {case.case_number}",
        )

    db.session.commit()

    result = {"added": added, "skipped": skipped, "total_added": len(added)}

    if request.is_json:
        return jsonify(result)

    if added:
        flash(f"Added {len(added)} subject(s) to case.", "success")
    if skipped:
        flash(
            f"Skipped {len(skipped)} subject(s) (already linked or not found).",
            "warning",
        )

    return redirect(url_for("cms.view_case", case_id=case_id))


@cms_bp.route("/cases/<case_id>/remove-subject/<subject_id>", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
@case_edit_required
def remove_subject_from_case(case_id: str, subject_id: str) -> flask.Response:
    """Remove a subject from a case."""
    case = db.session.get(Case, case_id) or abort(404)
    subject = db.session.get(Subject, subject_id) or abort(404)

    if subject not in case.subjects.all():
        return jsonify({"error": "Subject not linked to this case"}), 400

    case.subjects.remove(subject)

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="case",
        entity_id=case_id,
        description=f"Removed subject {subject.name} from case {case.case_number}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"message": "Subject removed from case"})

    flash(f"Subject {subject.name} removed from case.", "info")
    return redirect(url_for("cms.view_case", case_id=case_id))
