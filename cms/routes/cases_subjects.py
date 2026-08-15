import logging

import flask
from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from ..auth import (
    apply_tenant_filter,
    case_access_required,
    case_edit_required,
    ensure_tenant_access,
    roles_required,
)
from ..models import AuditLog, Case, Subject, db
from ..validation import AddSubjectToCaseSchema, BulkAddSubjectsSchema, validate
from . import cms_bp
from .response import api_error, api_success

logger = logging.getLogger(__name__)


@cms_bp.route("/cases/<case_id>/add-subject", methods=["POST"])
@login_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@case_access_required
@case_edit_required
@validate(AddSubjectToCaseSchema)
def add_subject_to_case(case_id: str) -> flask.Response:
    """Add an existing subject to a case."""
    case = db.session.get(Case, case_id) or abort(404)
    ensure_tenant_access(case)
    data = request.validated_data

    subject_id = data.get("subject_id")
    if not subject_id:
        return api_error("subject_id is required", 400)

    subject = db.session.get(Subject, subject_id) or abort(404)
    ensure_tenant_access(subject)
    if subject.is_deleted:
        abort(404)

    if subject in case.subjects.all():
        return api_error("Subject already linked to this case", 400)

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
@case_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@case_edit_required
@validate(BulkAddSubjectsSchema)
def bulk_add_subjects_to_case(case_id: str) -> flask.Response:
    """Add multiple subjects to a case at once."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    subject_ids = data.get("subject_ids", [])
    if not subject_ids:
        return api_error("subject_ids required", 400)

    if isinstance(subject_ids, str):
        subject_ids = [s.strip() for s in subject_ids.split(",")]

    # Batch-load subjects + existing links
    subjects = {
        s.id: s
        for s in apply_tenant_filter(
            Subject.query.filter(
                Subject.id.in_(subject_ids), Subject.is_deleted.is_(False)
            ),
            Subject,
        ).all()
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
@roles_required("admin", "owner", "senior_investigator")
@case_access_required
@case_edit_required
def remove_subject_from_case(case_id: str, subject_id: str) -> flask.Response:
    """Remove a subject from a case."""
    case = db.session.get(Case, case_id) or abort(404)
    ensure_tenant_access(case)
    subject = db.session.get(Subject, subject_id) or abort(404)
    ensure_tenant_access(subject)

    if subject not in case.subjects.all():
        return api_error("Subject not linked to this case", 400)

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
        return api_success({}, "Subject removed from case")

    flash(f"Subject {subject.name} removed from case.", "info")
    return redirect(url_for("cms.view_case", case_id=case_id))
