import logging
from datetime import datetime, timezone, timedelta

from flask import request, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, AuditLog, User, Setting
from ..auth import senior_required, admin_required, apply_tenant_filter

logger = logging.getLogger(__name__)


@cms_bp.route("/audit")
@login_required
@senior_required
def audit_log() -> str:
    """View audit log with filtering."""
    page = request.args.get("page", 1, type=int)
    per_page = 50
    entity_type = request.args.get("entity_type", "")
    action = request.args.get("action", "")
    user_id = request.args.get("user_id", "")
    case_id = request.args.get("case_id", "")
    search = request.args.get("search", "")

    query = apply_tenant_filter(
        AuditLog.query.options(db.joinedload(AuditLog.user)), AuditLog
    )

    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    if action:
        query = query.filter_by(action=action)
    if user_id:
        query = query.filter_by(user_id=user_id)
    if case_id:
        query = query.filter_by(case_id=case_id)
    if search:
        query = query.filter(
            db.or_(
                AuditLog.description.ilike(f"%{search}%"),
                AuditLog.action.ilike(f"%{search}%"),
            )
        )

    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Get filter options for dropdowns
    users = apply_tenant_filter(User.query.filter_by(is_active=True), User).all()
    entity_types = apply_tenant_filter(
        db.session.query(AuditLog.entity_type).distinct(), AuditLog
    ).all()
    entity_types = [e[0] for e in entity_types]
    actions = apply_tenant_filter(
        db.session.query(AuditLog.action).distinct(), AuditLog
    ).all()
    actions = [a[0] for a in actions]

    # Retention info
    retention_days = int(Setting.get("audit_log_retention_days", "365"))
    total_count = apply_tenant_filter(
        db.session.query(db.func.count(AuditLog.id)), AuditLog
    ).scalar()

    return render_template(
        "cms/audit/log.html",
        logs=pagination.items,
        pagination=pagination,
        filters={
            "entity_type": entity_type,
            "action": action,
            "user_id": user_id,
            "case_id": case_id,
            "search": search,
        },
        users=users,
        entity_types=entity_types,
        actions=actions,
        retention_days=retention_days,
        total_count=total_count,
    )


@cms_bp.route("/audit/purge", methods=["POST"])
@login_required
@admin_required
def audit_purge():
    """Manually purge audit logs older than the configured retention period."""
    retention_days = int(Setting.get("audit_log_retention_days", "365"))
    if retention_days <= 0:
        flash(
            "Audit log retention is set to keep logs forever. Set a positive retention period in settings first.",
            "warning",
        )
        return redirect(url_for("cms.audit_log"))

    # Purge across ALL tenants for super-admins, or scoped to tenant for regular admins
    if current_user.is_super_admin:
        deleted = AuditLog.purge_old(retention_days)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted = AuditLog.query.filter(
            AuditLog.tenant_id == current_user.tenant_id,
            AuditLog.timestamp < cutoff,
        ).delete()
        if deleted:
            db.session.commit()

    flash(
        f"Purged {deleted} audit log entries older than {retention_days} days.",
        "success",
    )
    return redirect(url_for("cms.audit_log"))
