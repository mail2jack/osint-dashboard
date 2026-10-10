"""Tenant-scoped event metering with an immutable audit trail.

This is intentionally a small, transparent first metering layer.  It records
the event counter and audit event in the caller's transaction; callers decide
when to commit.  No request data or research values are stored in the usage
record, and audit descriptions are redacted by ``AuditLog.log``.
"""

from datetime import date

from flask import has_request_context, request

from cms.models import AuditLog, UsageRecord


def record_usage_event(
    *,
    tenant_id: str,
    metric_name: str,
    audit_action: str,
    entity_type: str,
    entity_id: str | None = None,
    user_id: str | None = None,
    case_id: str | None = None,
    description: str | None = None,
    amount: int = 1,
):
    """Record one tenant-scoped event without committing the transaction."""
    UsageRecord.increment(tenant_id, date.today(), metric_name, amount)
    AuditLog.log(
        tenant_id=tenant_id,
        user_id=user_id,
        action=audit_action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        ip_address=request.remote_addr if has_request_context() else None,
        user_agent=request.user_agent.string if has_request_context() else None,
        description=description,
        new_values={"metric": metric_name, "amount": amount},
    )
