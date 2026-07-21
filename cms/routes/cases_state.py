import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import validate, SetCaseParentSchema, TransitionCaseSchema
from ..models import db, Case, AuditLog, CaseStatus
from ..auth import (
    roles_required,
    case_access_required,
    case_edit_required,
    ensure_tenant_access,
    apply_tenant_filter,
)

from .response import api_success, api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/cases/<case_id>/set-parent", methods=["POST"])
@login_required
@roles_required("admin", "owner", "senior_investigator")
@case_edit_required
@validate(SetCaseParentSchema)
def set_case_parent(case_id: str) -> flask.Response:
    """Set the parent case for a case."""
    case = db.session.get(Case, case_id) or abort(404)
    ensure_tenant_access(case)
    data = request.validated_data

    parent_id = data.get("parent_case_id")

    if parent_id:
        parent = db.session.get(Case, parent_id)
        if not parent or parent.is_deleted:
            return api_error("Parent case not found", 404)
        ensure_tenant_access(parent)

        if parent_id == case_id:
            return api_error("A case cannot be its own parent", 400)

        current = parent
        while current and current.parent_case_id:
            if current.parent_case_id == case_id:
                return api_error("This would create a circular reference", 400)
            current = current.parent_case

        old_parent_id = case.parent_case_id
        case.parent_case_id = parent_id

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="case",
            entity_id=case_id,
            changes={"parent_case": {"old": old_parent_id, "new": parent_id}},
            ip_address=request.remote_addr,
            description=f"Set parent case for {case.case_number} to {parent.case_number}",
        )
        db.session.commit()

        return jsonify(
            {
                "message": "Parent case set",
                "parent_case": {
                    "id": parent.id,
                    "case_number": parent.case_number,
                    "title": parent.title,
                },
            }
        )
    else:
        old_parent_id = case.parent_case_id
        case.parent_case_id = None

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="case",
            entity_id=case_id,
            changes={"parent_case": {"old": old_parent_id, "new": None}},
            ip_address=request.remote_addr,
            description=f"Removed parent case from {case.case_number}",
        )
        db.session.commit()

        return api_success({}, "Parent case removed")


@cms_bp.route("/api/cases/search")
@login_required
def search_cases() -> flask.Response:
    """Search cases for linking (excludes the current case)."""
    q = request.args.get("q", "")
    exclude_id = request.args.get("exclude_id", "")

    query = Case.query.filter_by(is_deleted=False)

    query = apply_tenant_filter(query, Case)

    if q:
        query = query.filter(
            db.or_(Case.case_number.ilike(f"%{q}%"), Case.title.ilike(f"%{q}%"))
        )

    if exclude_id:
        query = query.filter(Case.id != exclude_id)

    cases = query.order_by(Case.created_at.desc()).limit(20).all()

    return jsonify(
        {
            "cases": [
                {
                    "id": c.id,
                    "case_number": c.case_number,
                    "title": c.title,
                    "status": c.status,
                }
                for c in cases
            ]
        }
    )


@cms_bp.route("/api/cases/<case_id>/hierarchy")
@login_required
def get_case_hierarchy_api(case_id: str) -> flask.Response:
    """Get case hierarchy (parent and children) via API."""
    case = db.session.get(Case, case_id) or abort(404)
    ensure_tenant_access(case)
    return jsonify(
        {
            "parent": {
                "id": case.parent_case.id,
                "case_number": case.parent_case.case_number,
                "title": case.parent_case.title,
            }
            if case.parent_case
            else None,
            "children": [
                {
                    "id": c.id,
                    "case_number": c.case_number,
                    "title": c.title,
                    "status": c.status,
                }
                for c in case.child_cases.filter_by(is_deleted=False)
            ],
        }
    )


@cms_bp.route("/api/cases/<case_id>/audit-log")
@login_required
@case_access_required
def get_case_audit_log_api(case_id: str) -> flask.Response:
    """Get audit log for a case via API."""
    case = db.session.get(Case, case_id) or abort(404)
    ensure_tenant_access(case)
    query = AuditLog.query.filter_by(entity_type="case", entity_id=case_id)
    query = apply_tenant_filter(query, AuditLog)
    logs = query.order_by(AuditLog.created_at.desc()).limit(50).all()

    return jsonify(
        {
            "logs": [
                {
                    "action": log.action,
                    "description": log.description,
                    "user": log.user.full_name if log.user else "System",
                    "created_at": log.created_at.strftime("%Y-%m-%d %H:%M")
                    if log.created_at
                    else "",
                    "changes": log.changes,
                }
                for log in logs
            ]
        }
    )


@cms_bp.route("/cases/<case_id>/transition", methods=["POST"])
@login_required
@case_access_required
@case_edit_required
@validate(TransitionCaseSchema)
def transition_case(case_id: str) -> flask.Response:
    """Transition case to a new status."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    new_status = data.get("status")
    if not new_status:
        return api_error("Status is required", 400)

    old_status = case.status

    if new_status == CaseStatus.CLOSED.value:
        reason = data.get("closure_reason")
        if not reason:
            return api_error("Closure reason is required", 400)
        case.closure_reason = reason

    if (
        old_status in [CaseStatus.CLOSED.value, CaseStatus.ARCHIVED.value]
        and new_status == CaseStatus.ACTIVE.value
    ):
        reason = data.get("reopened_reason")
        if not reason:
            return api_error("Reopening reason is required", 400)
        case.reopened_reason = reason
        case.reopened_at = datetime.now(timezone.utc)
        case.reopened_by = current_user.id
        case.closure_reason = None

    if not case.transition_status(new_status, current_user.id):
        return jsonify(
            {"error": f"Cannot transition from {old_status} to {new_status}"}
        ), 400

    AuditLog.log(
        user_id=current_user.id,
        action="status_change",
        entity_type="case",
        entity_id=case_id,
        changes={"status": {"old": old_status, "new": new_status}},
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Case {case.case_number} status changed from {old_status} to {new_status}",
    )
    db.session.commit()

    return jsonify(
        {"message": f"Case transitioned to {new_status}", "case": case.to_dict()}
    )
