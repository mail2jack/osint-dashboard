import logging
from datetime import date, datetime, timezone, timedelta

from flask import url_for

from .models import (
    db,
    Tenant,
    User,
    Case,
    Subject,
    Client,
    Finding,
    Document,
    AuditLog,
    Screenshot,
    UsageRecord,
)

logger = logging.getLogger(__name__)

METRICS = [
    "users",
    "cases",
    "subjects",
    "clients",
    "findings",
    "documents",
    "audit_log_entries",
]


def aggregate_tenant(tenant_id: str, record_date: date) -> list[UsageRecord]:
    """Compute and store usage snapshot for a single tenant on a given date."""
    records = []

    counts = {
        "users": User.query.filter_by(tenant_id=tenant_id).count(),
        "cases": Case.query.filter_by(tenant_id=tenant_id).count(),
        "subjects": Subject.query.filter_by(tenant_id=tenant_id).count(),
        "clients": Client.query.filter_by(tenant_id=tenant_id).count(),
        "findings": Finding.query.filter_by(tenant_id=tenant_id).count(),
        "documents": Document.query.filter_by(tenant_id=tenant_id).count(),
        "audit_log_entries": AuditLog.query.filter_by(tenant_id=tenant_id).count(),
    }

    for metric_name, metric_value in counts.items():
        row = UsageRecord.upsert(tenant_id, record_date, metric_name, metric_value)
        records.append(row)

    db.session.commit()
    return records


def aggregate_all_tenants(record_date: date | None = None) -> int:
    """Compute usage snapshots for every active tenant.

    Returns the number of tenants aggregated.
    """
    if record_date is None:
        record_date = date.today()

    tenants = Tenant.query.filter_by(is_active=True).all()
    count = 0
    for tenant in tenants:
        try:
            aggregate_tenant(tenant.id, record_date)
            count += 1
        except Exception:
            logger.exception("Failed to aggregate usage for tenant %s", tenant.id)
            db.session.rollback()

    logger.info(
        "Aggregated usage for %d/%d active tenants on %s",
        count,
        len(tenants),
        record_date,
    )
    return count


def _get_tenant_storage_mb(tenant_id: str) -> int:
    """Return total storage used by a tenant in megabytes."""
    doc_bytes = (
        db.session.query(db.func.coalesce(db.func.sum(Document.file_size), 0))
        .filter(Document.tenant_id == tenant_id, Document.is_deleted == False)
        .scalar()
    )
    ss_bytes = (
        db.session.query(db.func.coalesce(db.func.sum(Screenshot.file_size), 0))
        .filter(Screenshot.tenant_id == tenant_id)
        .scalar()
    )
    return (doc_bytes + ss_bytes) // (1024 * 1024)


def check_and_alert_usage_limits(record_date: date | None = None) -> list[dict]:
    """Check all active tenants against tier limits and create notifications.

    Checks all 7 resource types plus storage. Sends in-app notifications
    when a tenant reaches 80% or 100% of their tier resource limits.

    Returns list of alerts triggered.
    """
    if record_date is None:
        record_date = date.today()

    from .tier_limits import TIERS, METRIC_TO_LIMIT_KEY

    alerts = []
    tenants = Tenant.query.filter_by(is_active=True).all()

    for tenant in tenants:
        limits = TIERS.get(tenant.tier, TIERS["free"])

        current_counts = {
            "users": User.query.filter_by(tenant_id=tenant.id).count(),
            "cases": Case.query.filter_by(tenant_id=tenant.id).count(),
            "subjects": Subject.query.filter_by(tenant_id=tenant.id).count(),
            "clients": Client.query.filter_by(tenant_id=tenant.id).count(),
            "findings": Finding.query.filter_by(tenant_id=tenant.id).count(),
            "documents": Document.query.filter_by(tenant_id=tenant.id).count(),
        }

        checks = []
        for metric, limit_key in METRIC_TO_LIMIT_KEY.items():
            maximum = limits.get(limit_key)
            checks.append((metric, current_counts[metric], maximum))

        checks.append(
            (
                "storage_mb",
                _get_tenant_storage_mb(tenant.id),
                limits.get("max_storage_mb"),
            )
        )

        admins = User.query.filter(
            User.tenant_id == tenant.id,
            User.is_active == True,
            User.role.in_(["admin", "owner"]),
        ).all()

        for resource, current, maximum in checks:
            if maximum is None:
                continue
            ratio = current / maximum if maximum > 0 else 0

            if ratio >= 1.0:
                alert_key = f"limit_reached_{tenant.id}_{resource}"
            elif ratio >= 0.8:
                alert_key = f"limit_warning_{tenant.id}_{resource}"
            else:
                continue

            from .models import Notification

            existing = Notification.query.filter(
                Notification.message.like(f"%{alert_key}%"),
                Notification.created_at
                >= datetime.now(timezone.utc) - timedelta(days=1),
            ).first()
            if existing:
                continue

            pct = int(ratio * 100)
            reached = ratio >= 1.0
            title = f"{'Limit reached' if reached else 'Limit warning'}: {resource}"

            unit = "MB" if resource == "storage_mb" else ""
            display = f"{current}/{maximum} {unit}".strip()
            message = (
                f"alert:{alert_key} | "
                f"Tenant '{tenant.name}' has {display} {resource} ({pct}%)."
            )

            for admin in admins:
                n = Notification(
                    tenant_id=tenant.id,
                    user_id=admin.id,
                    category="usage_alerts",
                    title=title,
                    message=message,
                    link=url_for("cms.settings", category="plan", _external=True),
                )
                db.session.add(n)

            db.session.commit()

            # Send email if admin has opted in for usage_alerts
            from .models import NotificationPreference
            from .email_utils import is_smtp_configured, send_email

            if is_smtp_configured():
                for admin in admins:
                    pref = NotificationPreference.get_pref(admin.id, "usage_alerts")
                    if pref.email_enabled and admin.email:
                        try:
                            send_email(
                                to_email=admin.email,
                                subject=title,
                                body_html=f"<p>{message}</p><p><a href='{url_for('cms.settings', category='plan', _external=True)}'>View plan</a></p>",
                                body_text=f"{message}\n\nView plan: {url_for('cms.settings', category='plan', _external=True)}",
                            )
                        except Exception:
                            logger.exception(
                                "Failed to email usage alert to %s", admin.email
                            )

            # Send SMS/WhatsApp for critical usage alerts
            from .notifications import send_alert_sms_whatsapp

            send_alert_sms_whatsapp(tenant, "usage_alerts", title, message)

            alerts.append(
                {
                    "tenant_id": tenant.id,
                    "resource": resource,
                    "current": current,
                    "maximum": maximum,
                    "pct": pct,
                    "reached": reached,
                }
            )
            logger.info("Usage alert for %s: %s at %d%%", tenant.name, resource, pct)

    return alerts


def aggregate_yesterday() -> int:
    """Convenience: aggregate for yesterday (for cron/CLI use)."""
    yesterday = date.today() - timedelta(days=1)
    return aggregate_all_tenants(yesterday)
