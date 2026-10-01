"""
Data retention — hard-delete tenant data after grace period expires.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import inspect

from .models import db, Tenant

logger = logging.getLogger(__name__)

# Models that hold per-tenant data, ordered so leaf tables come first
# (no other tenant-scoped table depends on them).
_PURGE_ORDER = [
    "finding_capture_jobs",
    "background_tasks",
    "finding_screenshots",
    "action_findings",
    "subject_identifiers",
    "subject_facts",
    "research_actions",
    "investigations",
    "case_number_counters",
    "investigation_seq_counters",
    "invoice_number_counters",
    "service_rates",
    "proration_logs",
    "audit_logs",
    "phone_lookups",
    "login_logs",
    "notification_preferences",
    "notifications",
    "comments",
    "comment_edit_history",
    "social_accounts",
    "financial_records",
    "invoice_items",
    "payments",
    "invoices",
    "screenshots",
    "spiderfoot_scans",
    "osint_searches",
    "api_keys",
    "reminders",
    "documents",
    "document_templates",
    "findings",
    "addresses",
    "contacts",
    "tenant_settings",
    "usage_records",
    "case_assignments",
    "case_subjects",
    "subjects",
    "clients",
    "cases",
    "users",
    "feature_flags",
]


def purge_expired_tenants(dry_run: bool = False) -> int:
    """Hard-delete all data for tenants past their ``scheduled_deletion_at``.

    Returns the number of tenants purged.
    """
    now = datetime.now(timezone.utc)
    expired = Tenant.query.filter(
        Tenant.scheduled_deletion_at.isnot(None),
        Tenant.scheduled_deletion_at <= now,
    ).all()

    if not expired:
        logger.info("No expired tenants to purge")
        return 0

    for tenant in expired:
        try:
            _purge_single_tenant(tenant, dry_run)
        except Exception:
            logger.exception("Failed to purge tenant %s (%s)", tenant.id, tenant.name)
            db.session.rollback()

    db.session.commit()
    logger.info("Purged %d expired tenant(s)", len(expired))
    return len(expired)


_ALLOWED_PURGE_TABLES = {
    "finding_capture_jobs",
    "background_tasks",
    "finding_screenshots",
    "action_findings",
    "subject_identifiers",
    "subject_facts",
    "research_actions",
    "investigations",
    "case_number_counters",
    "investigation_seq_counters",
    "invoice_number_counters",
    "service_rates",
    "proration_logs",
    "audit_logs",
    "finding",
    "subject_note",
    "subject_relation",
    "subject",
    "case",
    "client",
    "audit_log",
    "search_history",
    "export",
    "notification",
    "document",
    "comment",
    "event",
    "task",
    "invoice",
    "payment",
    "subscription",
    "workflow",
    "workflow_step",
    "osint_result",
    "phone_lookup",
    "email_lookup",
    "screenshot",
    "breach_record",
    "dpa_record",
    "user_session",
    "user_activity",
    "case_assignee",
    "case_tag",
    "phone_lookups",
    "login_logs",
    "notification_preferences",
    "notifications",
    "comments",
    "comment_edit_history",
    "social_accounts",
    "financial_records",
    "invoice_items",
    "payments",
    "invoices",
    "screenshots",
    "spiderfoot_scans",
    "osint_searches",
    "api_keys",
    "reminders",
    "documents",
    "document_templates",
    "findings",
    "addresses",
    "contacts",
    "tenant_settings",
    "usage_records",
    "case_assignments",
    "case_subjects",
    "subjects",
    "clients",
    "cases",
    "users",
    "feature_flags",
}


def _purge_single_tenant(tenant: Tenant, dry_run: bool) -> None:
    """Delete all rows belonging to *tenant* in the configured order."""
    tid = tenant.id
    inspector = inspect(db.engine)
    for table_name in _PURGE_ORDER:
        if table_name not in _ALLOWED_PURGE_TABLES:
            raise ValueError(f"Invalid table name: {table_name}")
        if not inspector.has_table(table_name):
            logger.warning("Purge table %s does not exist; skipping", table_name)
            continue
        if table_name == "action_findings":
            db.session.execute(
                db.text(
                    "DELETE FROM action_findings "
                    "WHERE action_id IN "
                    "(SELECT id FROM research_actions WHERE tenant_id = :tid)"
                ),
                {"tid": tid},
            )
        elif table_name == "notification_preferences":
            db.session.execute(
                db.text(
                    "DELETE FROM notification_preferences "
                    "WHERE user_id IN (SELECT id FROM users WHERE tenant_id = :tid)"
                ),
                {"tid": tid},
            )
        else:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "tenant_id" not in columns:
                logger.warning(
                    "Purge table %s has no tenant_id; relation-specific purge is required",
                    table_name,
                )
                continue
            db.session.execute(
                db.text(f"DELETE FROM {table_name} WHERE tenant_id = :tid"),
                {"tid": tid},
            )
        logger.debug("Purged %s for tenant %s", table_name, tid)

    if dry_run:
        db.session.rollback()
        logger.info(
            "[DRY RUN] Would have purged tenant %s (%s)", tenant.id, tenant.name
        )
    else:
        db.session.delete(tenant)
        logger.info("Purged tenant %s (%s)", tenant.id, tenant.name)
