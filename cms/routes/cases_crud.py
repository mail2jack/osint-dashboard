import logging
from datetime import datetime, date, timezone

import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import validate, CreateCaseSchema, EditCaseSchema, BulkDeleteSchema
from sqlalchemy.orm import joinedload
from ..models import db, Case, Client, Subject, AuditLog, User, CaseStatus, CasePriority
from ..auth import (
    roles_required,
    admin_required,
    case_access_required,
    case_edit_required,
    apply_tenant_filter,
)
from ..notifications import notify_case_created
from ..rate_limiting import rate_limit, STRICT_RATE_LIMIT

logger = logging.getLogger(__name__)


@cms_bp.route("/api/cases/bulk-delete", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
@validate(BulkDeleteSchema)
def bulk_delete_cases() -> flask.Response:
    """Soft-delete cases in bulk."""
    data = request.validated_data
    ids = data.get("ids", [])
    if not ids or len(ids) > 100:
        return jsonify({"error": "Provide a list of up to 100 case IDs"}), 400
    now = datetime.now(timezone.utc)
    count = Case.query.filter(Case.id.in_(ids), Case.is_deleted == False).update(
        {"is_deleted": True, "deleted_at": now}, synchronize_session=False
    )
    db.session.commit()
    AuditLog.log(
        user_id=current_user.id,
        action="bulk_delete",
        entity_type="case",
        ip_address=request.remote_addr,
        description=f"Bulk soft-deleted {count} cases",
    )
    return jsonify({"deleted": count, "message": f"{count} cases deleted"})


@cms_bp.route("/cases")
@login_required
def cases() -> str:
    """List all cases with filtering, sorting, and search."""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    search = request.args.get("search", "")
    client_filter = request.args.get("client", "")
    assigned = request.args.get("assigned", "")
    sort = request.args.get("sort", "case_number")
    order = request.args.get("order", "desc")

    query = (
        Case.query.filter_by(is_deleted=False)
        .join(Client)
        .options(joinedload(Case.client), joinedload(Case.lead_investigator))
    )

    # Tenant isolation (SQLite compat, RLS only works on Postgres)
    query = apply_tenant_filter(query, Case)

    # Non-admin users only see cases they can access
    if not current_user.is_admin:
        from ..models import case_assignments

        assigned_ids = (
            db.session.query(case_assignments.c.case_id)
            .filter(case_assignments.c.user_id == current_user.id)
            .all()
        )
        assigned_ids = [row[0] for row in assigned_ids]
        query = query.filter(
            db.or_(
                Case.created_by == current_user.id,
                Case.lead_investigator_id == current_user.id,
                Case.assigned_to == current_user.id,
                Case.id.in_(assigned_ids) if assigned_ids else False,
            )
        )

    if status:
        query = query.filter(Case.status == status)
    else:
        query = query.filter(Case.status != CaseStatus.CLOSED.value)

    if priority:
        query = query.filter(Case.priority == priority)

    case_type_filter = request.args.get("case_type", "")
    if case_type_filter:
        query = query.filter(Case.case_type.like(f"{case_type_filter}|%"))

    if client_filter:
        query = query.filter(Client.id == client_filter)

    if search:
        query = query.filter(
            db.or_(
                Case.title.ilike(f"%{search}%"),
                Case.case_number.ilike(f"%{search}%"),
                Case.description.ilike(f"%{search}%"),
                Client.name.ilike(f"%{search}%"),
            )
        )

    if assigned == "me" and not current_user.is_admin:
        from ..models import case_assignments

        assigned_ids = (
            db.session.query(case_assignments.c.case_id)
            .filter(case_assignments.c.user_id == current_user.id)
            .all()
        )
        assigned_ids = [row[0] for row in assigned_ids]

        query = query.filter(
            db.or_(
                Case.assigned_to == current_user.id,
                Case.id.in_(assigned_ids) if assigned_ids else False,
            )
        )

    sort_columns = {
        "case_number": Case.case_number,
        "title": Case.title,
        "client": Client.name,
        "priority": Case.priority,
        "status": Case.status,
        "created_at": Case.created_at,
        "updated_at": Case.updated_at,
    }

    sort_col = sort_columns.get(sort, Case.case_number)
    if order == "desc":
        sort_col = sort_col.desc()

    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )

    clients = (
        apply_tenant_filter(
            Client.query.filter_by(is_deleted=False, is_active=True), Client
        )
        .limit(500)
        .all()
    )

    return render_template(
        "cms/cases/list.html",
        cases=pagination.items,
        pagination=pagination,
        clients=clients,
        filters={
            "status": status,
            "priority": priority,
            "search": search,
            "client": client_filter,
            "assigned": assigned,
            "sort": sort,
            "order": order,
            "case_type": case_type_filter,
        },
    )


@cms_bp.route("/cases/create", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior_investigator", "investigator", "junior_investigator")
@rate_limit(STRICT_RATE_LIMIT, key_prefix="create_case")
@validate(CreateCaseSchema)
def create_case() -> flask.Response:
    """Create a new case."""
    clients = Client.query.filter_by(is_deleted=False, is_active=True).limit(500).all()
    investigators = User.query.filter(
        User.is_active == True,
        User.role.in_(["admin", "senior_investigator", "junior_investigator"]),
    ).all()

    is_json = request.is_json
    raw = request.get_json() if is_json else request.form

    def _error(msg):
        if is_json:
            return jsonify({"error": msg}), 400
        flash(msg, "error")
        return render_template(
            "cms/cases/create.html",
            clients=clients,
            investigators=investigators,
            title=raw.get("title", ""),
            client_id=raw.get("client_id", ""),
            lead_investigator_id=raw.get("lead_investigator_id", ""),
            description=raw.get("description", ""),
            priority=raw.get("priority", "medium"),
            case_type=raw.get("case_type", ""),
        )

    if request.method == "POST":
        data = request.validated_data

        required = ["title", "client_id"]
        for field in required:
            if not data.get(field):
                return _error(f"{field} is required")

        client = db.session.get(Client, data["client_id"])
        if not client or client.is_deleted:
            return _error("Invalid client")

        priority = data.get("priority", CasePriority.MEDIUM.value)
        valid_priorities = {v.value for v in CasePriority}
        if priority not in valid_priorities:
            return _error(
                f"Invalid priority. Must be one of: {', '.join(sorted(valid_priorities))}"
            )

        case = Case(
            case_number=Case.generate_case_number(),
            client_id=data["client_id"],
            title=data["title"],
            description=data.get("description"),
            priority=priority,
            status=CaseStatus.OPEN.value,
            start_date=data.get("start_date", date.today()),
            target_end_date=data.get("target_end_date"),
            case_type=data.get("case_type"),
            jurisdiction=data.get("jurisdiction"),
            tags=data.get("tags"),
            created_by=current_user.id,
            lead_investigator_id=data.get("lead_investigator_id") or None,
        )

        db.session.add(case)
        db.session.flush()

        if data.get("assigned_to"):
            case.assigned_to = data["assigned_to"]

        if data.get("subject_ids"):
            subjects_map = {}
            for s in Subject.query.filter(Subject.id.in_(data["subject_ids"])).all():
                subjects_map[s.id] = s
            for subject_id in data["subject_ids"]:
                subject = subjects_map.get(subject_id)
                if subject:
                    case.subjects.append(subject)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="case",
            entity_id=case.id,
            new_values={
                "case_number": case.case_number,
                "title": case.title,
                "client_id": case.client_id,
            },
            ip_address=request.remote_addr,
            case_id=case.id,
            description=f"Created case: {case.case_number} - {case.title}",
        )
        db.session.commit()

        notify_case_created(case.id, case.title, current_user.username)

        if request.is_json:
            return jsonify({"message": "Case created", "case": case.to_dict()}), 201

        flash(f"Case {case.case_number} created successfully.", "success")
        return redirect(url_for("cms.view_case", case_id=case.id))

    return render_template(
        "cms/cases/create.html",
        clients=clients,
        investigators=investigators,
        title="",
        client_id="",
        lead_investigator_id="",
        description="",
        priority="medium",
        case_type="",
    )


@cms_bp.route("/cases/<case_id>/edit", methods=["GET", "POST"])
@login_required
@case_access_required
@case_edit_required
@validate(EditCaseSchema)
def edit_case(case_id: str) -> flask.Response:
    """Edit case details."""
    case = db.session.get(Case, case_id) or abort(404)
    clients = (
        apply_tenant_filter(
            Client.query.filter_by(is_deleted=False, is_active=True), Client
        )
        .limit(500)
        .all()
    )
    investigators = User.query.filter(
        User.is_active == True,
        User.role.in_(["admin", "senior_investigator", "junior_investigator"]),
    ).all()
    is_json = request.is_json

    if request.method == "POST":
        data = request.validated_data
        changes = {}

        editable_fields = [
            "title",
            "description",
            "priority",
            "case_type",
            "jurisdiction",
        ]

        for field in editable_fields:
            if field in data:
                old_value = getattr(case, field)
                new_value = data[field]
                if new_value != old_value:
                    if field == "priority":
                        valid_priorities = {v.value for v in CasePriority}
                        if new_value not in valid_priorities:
                            if is_json:
                                return jsonify(
                                    {
                                        "error": f"Invalid priority. Must be one of: {', '.join(sorted(valid_priorities))}"
                                    }
                                ), 400
                            flash("Invalid priority value.", "danger")
                            return render_template(
                                "cms/cases/edit.html",
                                case=case,
                                clients=clients,
                                investigators=investigators,
                            )
                    changes[field] = {
                        "old": str(old_value) if old_value else None,
                        "new": str(new_value),
                    }
                    setattr(case, field, new_value)

        if "tags" in data:
            tags_value = data["tags"]
            if isinstance(tags_value, str):
                new_tags = [t.strip() for t in tags_value.split(",") if t.strip()]
            elif isinstance(tags_value, list):
                new_tags = tags_value
            else:
                new_tags = []

            old_tags = case.tags or []
            if sorted(new_tags) != sorted(old_tags):
                changes["tags"] = {"old": old_tags, "new": new_tags}
                case.tags = new_tags if new_tags else None

        new_lead = data.get("lead_investigator_id") or None
        if new_lead != case.lead_investigator_id:
            changes["lead_investigator_id"] = {
                "old": case.lead_investigator_id,
                "new": new_lead,
            }
            case.lead_investigator_id = new_lead

        if "target_end_date" in data and data["target_end_date"]:
            try:
                from datetime import datetime as dt

                new_date = dt.strptime(data["target_end_date"], "%Y-%m-%d").date()
                old_date = case.target_end_date
                if new_date != old_date:
                    changes["target_end_date"] = {
                        "old": str(old_date) if old_date else None,
                        "new": str(new_date),
                    }
                    case.target_end_date = new_date
            except ValueError:
                logger.debug("Invalid date value for target_end_date")

        # Handle status transition (independent of target_end_date)
        if "status" in data and data["status"]:
            if case.transition_status(data["status"], current_user.id):
                changes["status"] = {"old": case.status, "new": data["status"]}
            else:
                if is_json:
                    return jsonify({"error": "Invalid status transition"}), 400
                flash("Invalid status transition.", "danger")
                return render_template(
                    "cms/cases/edit.html",
                    case=case,
                    clients=clients,
                    investigators=investigators,
                )

        case.updated_at = datetime.now(timezone.utc)

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="case",
            entity_id=case_id,
            changes=changes,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Updated case: {case.case_number}",
        )
        db.session.commit()

        if is_json:
            return jsonify({"message": "Case updated", "case": case.to_dict()})

        flash("Case updated successfully.", "success")
        return redirect(url_for("cms.view_case", case_id=case.id))

    return render_template(
        "cms/cases/edit.html", case=case, clients=clients, investigators=investigators
    )


@cms_bp.route("/cases/<case_id>/archive", methods=["POST"])
@login_required
@admin_required
def archive_case(case_id: str) -> flask.Response:
    """Archive a closed case."""
    case = db.session.get(Case, case_id) or abort(404)

    if case.status != CaseStatus.CLOSED.value:
        return jsonify({"error": "Only closed cases can be archived"}), 400

    case.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="archive",
        entity_type="case",
        entity_id=case_id,
        ip_address=request.remote_addr,
        description=f"Archived case: {case.case_number}",
    )
    db.session.commit()

    flash(f"Case {case.case_number} has been archived.", "info")
    return redirect(url_for("cms.cases"))
