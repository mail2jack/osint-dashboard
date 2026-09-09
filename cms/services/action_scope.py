"""ADR-0005 service helpers for scoping ``ResearchAction`` to an ``Investigation``.

The database hard-enforces the same-case / same-tenant invariant with the
composite FK from PR-A (migration ``f5a6b7c8d9e0``). Service-side validation
mirrors that so API callers get a clean 400/404 instead of a 500 from an
``IntegrityError``, and it adds the one rule the DB cannot express: only
**open** (non-archived) investigations are link targets.

Archiving an investigation never unlinks existing actions (history is kept);
only a new deliberate link/unlink write changes scope, and every scope change
is logged via ``AuditLog`` (ADR-0005 D3/D4).
"""

from cms.models import AuditLog, Investigation, InvestigationStatus, db


def get_linkable_investigation(investigation_id, *, case, tenant_id):
    """Resolve and validate an investigation as a link target for a case.

    Returns ``(investigation, error, status)``. ``investigation`` is the model
    when the id is ``None`` or a valid open same-case/same-tenant
    investigation, otherwise ``None`` plus a client-safe message and HTTP
    status. ``status`` of 404 means the investigation does not exist at all;
    400 covers wrong-case, wrong-tenant and archived.
    """
    if not investigation_id:
        return None, None, None

    investigation = db.session.get(Investigation, investigation_id)
    if investigation is None:
        return None, "Investigation not found", 404

    if investigation.case_id != case.id:
        return None, "Investigation does not belong to this case", 400
    if tenant_id and investigation.tenant_id != tenant_id:
        return None, "Investigation does not belong to this tenant", 400
    # Positive OPEN check (ADR-0005 D6): linkable only while status is exactly
    # OPEN AND archived_at is NULL. A non-open status without an archived_at
    # (e.g. a future status) stays non-linkable too.
    is_open = (
        investigation.status == InvestigationStatus.OPEN.value
        and investigation.archived_at is None
    )
    if not is_open:
        return None, "Only open (non-archived) investigations can be linked", 400

    return investigation, None, None


def action_scope_label(action) -> str:
    """Human label of an action's scope (ADR-0005 D1/D5)."""
    return f"linked to investigation {action.investigation_id}" if action.investigation_id else "case-wide"


def log_scope_audit(
    *,
    action,
    audit_action,
    user_id,
    ip_address,
    case_id,
    description,
    old_investigation_id=None,
    new_investigation_id=None,
):
    """Audit a research-action create or a scope change (ADR-0005 D3).

    Always records the relevant investigation ids in ``old_values`` /
    ``new_values`` so the scope is auditable end-to-end.
    """
    old_values = {"investigation_id": old_investigation_id}
    new_values = {"investigation_id": new_investigation_id}
    AuditLog.log(
        user_id=user_id,
        action=audit_action,
        entity_type="research_action",
        entity_id=action.id,
        ip_address=ip_address,
        case_id=case_id,
        old_values=old_values,
        new_values=new_values,
        description=description,
    )