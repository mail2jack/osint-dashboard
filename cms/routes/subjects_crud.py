import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import (
    validate,
    CreateSubjectSchema,
    EditSubjectSchema,
    BulkDeleteSchema,
)
from ..models import db, Subject, Case, AuditLog, case_subjects
from ..auth import (
    roles_required,
    subject_access_required,
    apply_tenant_filter,
    ensure_tenant_access,
)
from .utils import find_similar_subjects, check_for_exact_match
from ..services.subject_service import subject_service, compute_display_name
from ..rate_limiting import rate_limit, STRICT_RATE_LIMIT
from ..tier_limits import check_resource_limit, check_feature

from .response import api_success, api_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@cms_bp.route("/subjects/create", methods=["GET", "POST"])
@login_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@rate_limit(STRICT_RATE_LIMIT, key_prefix="create_subject")
@validate(CreateSubjectSchema)
def create_subject() -> flask.Response:
    """Create a new subject with duplicate detection."""
    # ADR-0001 rollout: standalone legacy create is gated once a tenant is on
    # Subject-First. Adds from a workflow case (case_id in query or form) stay
    # active.
    case_id = request.args.get("case_id") or request.validated_data.get("case_id")
    if not case_id and check_feature("subject_first_investigations"):
        if request.is_json:
            return api_error("Use the Subject Profile to add a subject.", 403)
        flash(
            "Subject-First is enabled for this tenant; add subjects from the Subject Profile.",
            "info",
        )
        return redirect(url_for("cms.subjects"))

    if request.method == "POST":
        data = request.validated_data
        if "type_" in data:
            data["type"] = data.pop("type_")

        required = ["subject_type"]
        for field in required:
            if not data.get(field):
                if request.is_json:
                    return api_error(f"{field} is required", 400)
                flash(f"{field} is required.", "danger")
                return render_template("cms/subjects/create.html")

        # Compute name from split fields if not provided (server-side,
        # shared with the service).
        if not data.get("name"):
            data["name"] = compute_display_name(data)

        if not data.get("name"):
            if request.is_json:
                return api_error("name is required", 400)
            flash("name is required.", "danger")
            return render_template("cms/subjects/create.html")

        name = data["name"].strip()

        # Auto-prepend @ for online entity names
        if data.get("subject_type") == "online" and name and not name.startswith("@"):
            name = "@" + name
            data["name"] = name

        # Check for duplicates
        exact_match = check_for_exact_match(name, "subject")
        similar = find_similar_subjects(name)

        # Skip duplicate check if already confirmed
        if not data.get("confirm_duplicate"):
            if exact_match:
                if request.is_json:
                    return jsonify(
                        {
                            "error": "exact_match",
                            "message": f"A subject with this name already exists: {exact_match['name']}",
                            "duplicate": exact_match,
                            "similar": similar[:5],
                        }
                    ), 409
                flash(
                    f"Warning: A subject with this name already exists: {exact_match['name']}",
                    "warning",
                )
                case_id = request.args.get("case_id")
                return render_template(
                    "cms/subjects/create.html",
                    case_id=case_id,
                    duplicate_warning=True,
                    exact_match=exact_match,
                    similar_subjects=similar[:5],
                    submitted_name=name,
                    submitted_type=data.get("subject_type"),
                )

            if similar and not request.is_json:
                flash(
                    "Warning: Similar subjects found. Please review before creating.",
                    "warning",
                )
                case_id = request.args.get("case_id")
                return render_template(
                    "cms/subjects/create.html",
                    case_id=case_id,
                    duplicate_warning=True,
                    similar_subjects=similar[:5],
                    submitted_name=name,
                    submitted_type=data.get("subject_type"),
                )

        # Check subject limit before creating
        ok, cur, maximum = check_resource_limit(Subject, "tenant_id", "max_subjects")
        if not ok:
            if request.is_json:
                return api_error(
                    f"Subject limit reached ({cur}/{maximum}). Upgrade your plan to add more subjects.",
                    403,
                )
            flash(
                f"Subject limit reached ({cur}/{maximum}). Upgrade your plan to add more subjects.",
                "danger",
            )
            return render_template("cms/subjects/create.html")

        # Build + persist through the shared service (single write path).
        subject = subject_service.create(
            data, created_by=current_user.id, tenant_id=current_user.tenant_id
        )

        # Link to case if specified
        if data.get("case_id"):
            case = db.session.get(Case, data["case_id"])
            if case:
                ensure_tenant_access(case)
                case.subjects.append(subject)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="subject",
            entity_id=subject.id,
            new_values={"name": subject.name, "type": subject.subject_type},
            ip_address=request.remote_addr,
            case_id=data.get("case_id"),
            description=f"Created subject ({subject.subject_type})",
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create subject")
            if request.is_json:
                return api_error("Failed to create subject", 500)
            flash("Failed to create subject.", "danger")
            return render_template("cms/subjects/create.html")

        try:
            from ..webhooks import dispatch

            dispatch(
                "subject.created",
                {
                    "id": subject.id,
                    "name": subject.name,
                    "subject_type": subject.subject_type,
                },
            )
        except Exception:
            logger.debug("Webhook dispatch failed for subject.created", exc_info=True)

        if request.is_json:
            return jsonify(
                {"message": "Subject created", "subject": subject.to_dict()}
            ), 201

        flash(f"Subject {subject.name} created successfully.", "success")

        # If created from case view, redirect back to case
        if data.get("case_id"):
            return redirect(url_for("cms.view_case", case_id=data["case_id"]))

        return redirect(url_for("cms.view_subject", subject_id=subject.id))

    # Pass case_id from query param if coming from case view
    case_id = request.args.get("case_id")
    cases = []
    if not case_id:
        q = apply_tenant_filter(Case.query, Case)
        cases = (
            q.filter(Case.is_deleted == False).order_by(Case.case_number.desc()).all()
        )
    return render_template("cms/subjects/create.html", case_id=case_id, cases=cases)


@cms_bp.route("/subjects/<subject_id>/edit", methods=["GET", "POST"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@rate_limit(STRICT_RATE_LIMIT, key_prefix="edit_subject")
@validate(EditSubjectSchema)
def edit_subject(subject_id: str) -> flask.Response:
    """Edit subject details."""
    # ADR-0001 rollout: once a tenant is on Subject-First, the legacy edit
    # screen is replaced by the Subject Profile tabs (identity/contact edits
    # happen there).
    if check_feature("subject_first_investigations"):
        if request.is_json:
            return api_error("Use the subject profile API to edit this subject.", 403)
        flash(
            "Subject details are edited on the Subject Profile.",
            "info",
        )
        return redirect(url_for("cms.subject_profile", subject_id=subject_id))

    subject = db.session.get(Subject, subject_id) or abort(404)

    if request.method == "POST":
        data = request.validated_data
        if "type_" in data:
            data["type"] = data.pop("type_")

        # Apply through the shared service (single write path); returns the
        # audit diff for every changed field.
        try:
            changes = subject_service.edit(subject, data, actor_id=current_user.id)
        except ValueError as e:
            db.session.rollback()
            if request.is_json:
                return api_error(str(e), 400)
            flash(str(e), "danger")
            return redirect(url_for("cms.edit_subject", subject_id=subject_id))

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="subject",
            entity_id=subject_id,
            changes=changes,
            ip_address=request.remote_addr,
            description=f"Updated subject ({subject.subject_type})",
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update subject")
            if request.is_json:
                return api_error("Failed to update subject", 500)
            flash("Failed to update subject.", "danger")
            return redirect(url_for("cms.edit_subject", subject_id=subject_id))

        if request.is_json:
            return jsonify({"message": "Subject updated", "subject": subject.to_dict()})

        flash("Subject updated successfully.", "success")
        return redirect(url_for("cms.view_subject", subject_id=subject.id))

    # Render with autoflush off: the template re-queries dynamic relationships
    # (subject.addresses/contacts); an autoflush would re-encrypt the freshly
    # decrypted values mid-render and show ciphertext. The view's commit still
    # flushes, so the before_flush guard re-encrypts before anything persists.
    with db.session.no_autoflush:
        subject.decrypt_identifiers()
        addresses = list(subject.addresses)
        for addr in addresses:
            addr.decrypt_fields()
        contacts = list(subject.contacts)
        for c in contacts:
            c.decrypt_fields()
        return render_template(
            "cms/subjects/edit.html",
            subject=subject,
            addresses=addresses,
            contacts=contacts,
        )


@cms_bp.route("/api/subjects/bulk-delete", methods=["POST"])
@login_required
@roles_required("admin", "owner", "senior_investigator")
@validate(BulkDeleteSchema)
def bulk_delete_subjects() -> flask.Response:
    """Soft-delete subjects in bulk (consistent with single delete)."""
    data = request.validated_data
    ids = data.get("ids", [])
    if not ids or len(ids) > 100:
        return api_error("Provide a list of up to 100 subject IDs", 400)
    now = datetime.now(timezone.utc)
    count = apply_tenant_filter(
        Subject.query.filter(Subject.id.in_(ids), Subject.is_deleted == False),
        Subject,
    ).update({"is_deleted": True, "deleted_at": now}, synchronize_session=False)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to bulk delete subjects")
        return api_error("Failed to delete subjects", 500)
    AuditLog.log(
        user_id=current_user.id,
        action="bulk_delete",
        entity_type="subject",
        ip_address=request.remote_addr,
        description=f"Bulk soft-deleted {count} subjects",
    )
    return jsonify({"deleted": count, "message": f"{count} subjects deleted"})


@cms_bp.route("/subjects/<subject_id>/delete", methods=["POST"])
@login_required
@subject_access_required
@roles_required("admin", "owner", "senior_investigator")
def delete_subject(subject_id: str) -> flask.Response:
    """Soft-delete a subject if not linked to any case."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    # Check if subject is linked to any active case
    linked_case_ids = [
        row.case_id
        for row in db.session.query(case_subjects.c.case_id)
        .filter(case_subjects.c.subject_id == subject_id)
        .all()
    ]
    linked_cases = (
        Case.query.filter(Case.id.in_(linked_case_ids), Case.is_deleted == False).all()
        if linked_case_ids
        else []
    )
    if linked_cases:
        case_list = ", ".join(
            [f"{c.case_number} ({c.title})" for c in linked_cases[:5]]
        )
        extra = f" and {len(linked_cases) - 5} more" if len(linked_cases) > 5 else ""
        return jsonify(
            {
                "error": f"Cannot delete subject: linked to {len(linked_cases)} case(s): {case_list}{extra}"
            }
        ), 400

    subject.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="subject",
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Deleted subject ({subject.subject_type})",
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete subject")
        if request.is_json:
            return api_error("Failed to delete subject", 500)
        flash("Failed to delete subject.", "danger")
        return redirect(url_for("cms.subjects"))

    if request.is_json:
        return api_success({}, "Subject deleted")
    flash(f"Subject {subject.name} deleted.", "info")
    return redirect(url_for("cms.subjects"))
