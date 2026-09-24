"""Bounded worker operations for workflow-native passive source research.

The worker starts at most one queued SpiderFoot scan or refreshes at most one
running scan per invocation.  It must run outside Gunicorn with an explicit
RLS bypass because it operates across tenants; the rows themselves retain
their tenant, case, investigation and action references.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa

from cms.models import ActionFinding, Finding, Notification, SpiderFootScan, db
from cms.tenant_context import set_tenant_context
from cms.tier_limits import check_feature
from cms.workflow.models import WorkflowResearchAction

logger = logging.getLogger(__name__)

_QUEUED_PREFIX = "queued:"
_TERMINAL = {"completed", "failed", "cancelled"}


def _notification_link(action: WorkflowResearchAction) -> str:
    """Build a local workflow link without exposing third-party internals."""
    if action.investigation_id:
        return (
            f"/cms/workflow/case/{action.case_id}/investigations/"
            f"{action.investigation_id}#investigation-findings"
        )
    return f"/cms/workflow/case/{action.case_id}#case-findings"


def _notify_terminal(action: WorkflowResearchAction, *, completed: bool, count: int = 0) -> None:
    """Create one durable, user-owned terminal notification in this transaction."""
    if not action.created_by:
        return
    db.session.add(
        Notification(
            tenant_id=action.tenant_id,
            user_id=action.created_by,
            category="source_research",
            title=("Verdiept bronnenonderzoek klaar" if completed else "Verdiept bronnenonderzoek mislukt"),
            message=(
                f"{count} nieuwe bevindingen zijn beschikbaar."
                if completed
                else "De vastlegging kon niet worden voltooid. Probeer het later opnieuw."
            ),
            link=_notification_link(action),
        )
    )


def _worker_context() -> None:
    """Establish the only cross-tenant context used by this worker."""
    set_tenant_context(db, None, bypass_rls=True)


def get_source_research_service():
    """Construct the configured SpiderFoot client without importing a route."""
    from cms.setting_cache import cached_setting_get
    from cms.spiderfoot_service import SpiderFootConfig, SpiderFootService

    return SpiderFootService(
        SpiderFootConfig(
            base_url=cached_setting_get("spiderfoot_url", "http://localhost:5001")
            or "http://localhost:5001",
            username=cached_setting_get("spiderfoot_username", "admin") or "admin",
            password=cached_setting_get("spiderfoot_password", "") or "",
        )
    )


def _next_scan(*, status: str, queued_only: bool = False) -> SpiderFootScan | None:
    statement = (
        sa.select(SpiderFootScan)
        .where(
            SpiderFootScan.research_action_id.is_not(None),
            SpiderFootScan.is_deleted.is_(False),
            SpiderFootScan.status == status,
        )
        .order_by(SpiderFootScan.created_at, SpiderFootScan.id)
        .limit(1)
    )
    if queued_only:
        statement = statement.where(SpiderFootScan.scan_id.startswith(_QUEUED_PREFIX))
    elif status == "running":
        # A process can be interrupted after it claims a local placeholder but
        # before SpiderFoot returns its real scan id.  Such a record is not
        # pollable; do not let it starve an already-running real scan.
        statement = statement.where(~SpiderFootScan.scan_id.startswith(_QUEUED_PREFIX))
    if db.session.bind and db.session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    return db.session.execute(statement).scalar_one_or_none()


def _set_failed(scan_id: str, message: str) -> None:
    """Record a bounded, non-sensitive failure on both linked records."""
    _worker_context()
    scan = db.session.get(SpiderFootScan, scan_id)
    if scan is None:
        db.session.rollback()
        return
    scan.update_status("failed")
    action = db.session.get(WorkflowResearchAction, scan.research_action_id)
    if action is not None:
        action.status = "error"
        action.error = message[:500]
        action.completed_at = datetime.now(timezone.utc)
        action.result_summary = "Source research could not be started"
        _notify_terminal(action, completed=False)
    db.session.commit()


def _scan_status(raw: object) -> tuple[str, int | None]:
    """Map permissive SpiderFoot API status payloads to local lifecycle values."""
    if not isinstance(raw, dict):
        return "running", None
    value = str(raw.get("status") or raw.get("state") or "running").lower()
    status = {
        "finished": "completed",
        "complete": "completed",
        "completed": "completed",
        "error": "failed",
        "failed": "failed",
        "aborted": "cancelled",
        "cancelled": "cancelled",
    }.get(value, "running")
    progress = raw.get("progress")
    return status, progress if isinstance(progress, int) and 0 <= progress <= 100 else None


def _safe_proposals(results: list[dict]) -> list[dict]:
    """Store a bounded review snapshot; never retain SpiderFoot raw payloads."""
    proposals = []
    for result in results[:250]:
        if not isinstance(result, dict):
            continue
        proposals.append(
            {
                "type": str(result.get("type") or "UNKNOWN")[:100],
                "data": str(
                    result.get("data") or result.get("dataTransformed") or ""
                )[:2000],
                "source_module": str(result.get("sourceModule") or "")[:200],
                "source_url": str(result.get("sourceUrl") or "")[:2000],
            }
        )
    return proposals


def _materialize_completed_proposals(
    scan: SpiderFootScan, action: WorkflowResearchAction
) -> int:
    """Create ordinary unverified findings for completed source proposals.

    The index ledger in ``result_summary`` makes this safe to run repeatedly:
    an interrupted or later worker tick never duplicates a finding.
    """
    state = dict(scan.result_summary) if isinstance(scan.result_summary, dict) else {}
    proposals = state.get("proposals")
    if not isinstance(proposals, list):
        return 0
    imported = {
        index
        for index in state.get("imported_indexes", [])
        if isinstance(index, int) and not isinstance(index, bool)
    }
    created = 0
    for index, proposal in enumerate(proposals):
        if index in imported or not isinstance(proposal, dict):
            continue
        event_type = str(proposal.get("type") or "UNKNOWN")[:100]
        data = str(proposal.get("data") or "")[:2000]
        if not data:
            imported.add(index)
            continue
        module = str(proposal.get("source_module") or "")[:200]
        source_url = str(proposal.get("source_url") or "")[:2000] or None
        finding = Finding(
            tenant_id=action.tenant_id,
            case_id=action.case_id,
            subject_id=action.subject_id,
            title=f"Verdiept bronnenonderzoek · {event_type}: {data[:100]}",
            content=(f"Bron: Verdiept bronnenonderzoek\nType: {event_type}\nData: {data}"
                     + (f"\nModule: {module}" if module else "")),
            detail=data,
            source_url=source_url,
            source_type="spiderfoot",
            icon="🕷️",
            verified=False,
            status="candidate",
            raw_data={"source_research_action_id": action.id,
                      "proposal_index": index,
                      "event_type": event_type,
                      "source_module": module},
            created_by=action.created_by,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(finding)
        db.session.flush()
        db.session.add(ActionFinding(action_id=action.id, finding_id=finding.id))
        imported.add(index)
        created += 1
    state["imported_indexes"] = sorted(imported)
    scan.result_summary = state
    return created


def start_one_source_research() -> str:
    """Claim and start at most one queued passive scan, never from a request."""
    _worker_context()
    scan = _next_scan(status="pending", queued_only=True)
    if scan is None:
        db.session.commit()
        return "idle"
    scan_id = scan.id
    action_id = scan.research_action_id
    tenant_id = scan.tenant_id
    use_case = scan.use_case or "passive"
    scan.update_status("running", progress=0)
    action = db.session.get(WorkflowResearchAction, action_id)
    if action is None:
        db.session.rollback()
        _set_failed(scan_id, "Source research action is unavailable")
        return "failed"
    try:
        request_metadata = json.loads(action.target_snapshot or "{}")
    except (TypeError, ValueError):
        request_metadata = {}
    expert_mode = bool(request_metadata.get("expert_mode"))
    action.status = "running"
    action.started_at = datetime.now(timezone.utc)
    action.result_summary = "Verdiept bronnenonderzoek wordt gestart"
    db.session.commit()

    # A flag may have been disabled after the request was stored.  Re-check at
    # execution time, before any external connection is attempted.
    _worker_context()
    if not (
        check_feature("workflow_spiderfoot", tenant_id)
        and check_feature("spiderfoot", tenant_id)
    ):
        _set_failed(scan_id, "Source research is disabled for this tenant")
        return "disabled"
    if use_case != "passive" and not check_feature(
        "workflow_source_research_intensity", tenant_id
    ):
        _set_failed(scan_id, "Advanced source research is disabled for this tenant")
        return "disabled"
    if expert_mode and not check_feature("workflow_source_research_expert", tenant_id):
        _set_failed(scan_id, "Expert source research is disabled for this tenant")
        return "disabled"

    try:
        service = get_source_research_service()
        if not service.is_available():
            _set_failed(scan_id, "Source research service is unavailable")
            return "failed"
        # Reload the record under the worker context after the external client
        # initialization: a pooled connection may have changed.
        _worker_context()
        current = db.session.get(SpiderFootScan, scan_id)
        if current is None or current.status != "running":
            db.session.rollback()
            return "cancelled"
        result = service.start_scan(
            target=current.target_value,
            target_type=current.target_type,
            scan_name=current.scan_name,
            use_case=current.use_case or "passive",
            module_ids=list(current.module_ids or []),
        )
        real_scan_id = result.get("scan_id") if isinstance(result, dict) else None
        if not isinstance(real_scan_id, str) or not real_scan_id:
            _set_failed(scan_id, "Source research service did not accept the request")
            return "failed"
        current.scan_id = real_scan_id[:100]
        current.status = "running"
        action = db.session.get(WorkflowResearchAction, action_id)
        if action is not None:
            action.result_summary = "Verdiept bronnenonderzoek loopt"
        db.session.commit()
        return "started"
    except Exception:
        logger.exception("Workflow source-research start failed scan_id=%s", scan_id)
        db.session.rollback()
        _set_failed(scan_id, "Source research worker could not start the scan")
        return "failed"


def refresh_one_source_research() -> str:
    """Refresh at most one running source-research scan and cache proposals."""
    _worker_context()
    scan = _next_scan(status="running")
    if scan is None or scan.scan_id.startswith(_QUEUED_PREFIX):
        db.session.commit()
        return "idle"
    scan_id = scan.id
    external_id = scan.scan_id
    db.session.commit()

    try:
        service = get_source_research_service()
        status_payload = service.get_scan_status(external_id)
        status, progress = _scan_status(status_payload)
        if status == "running":
            _worker_context()
            current = db.session.get(SpiderFootScan, scan_id)
            if current is not None and current.status == "running" and progress is not None:
                current.progress = progress
                db.session.commit()
            else:
                db.session.rollback()
            return "running"

        proposals: list[dict] = []
        summary: dict = {}
        if status == "completed":
            results = service.get_scan_results(external_id, limit=250)
            proposals = _safe_proposals(results)
            summary = service.get_result_summary(results)

        _worker_context()
        current = db.session.get(SpiderFootScan, scan_id)
        if current is None or current.status != "running":
            db.session.rollback()
            return "cancelled"
        current.update_status(status, progress=100 if status == "completed" else None)
        current.result_count = len(proposals)
        current.result_summary = {"summary": summary, "proposals": proposals}
        action = db.session.get(WorkflowResearchAction, current.research_action_id)
        if action is not None:
            created = (
                _materialize_completed_proposals(current, action)
                if status == "completed"
                else 0
            )
            action.status = "completed" if status == "completed" else "error"
            action.completed_at = datetime.now(timezone.utc)
            action.result_summary = (
                f"{created} kandidaatbevindingen toegevoegd vanuit verdiept bronnenonderzoek"
                if status == "completed"
                else "Verdiept bronnenonderzoek is niet voltooid"
            )
            if status != "completed":
                action.error = "Verdiept bronnenonderzoek is niet voltooid"
            _notify_terminal(action, completed=status == "completed", count=created)
        db.session.commit()
        return status
    except Exception:
        logger.exception("Workflow source-research refresh failed scan_id=%s", scan_id)
        db.session.rollback()
        # A transient status failure is retained as running.  We never discard
        # a scan just because a single poll could not reach SpiderFoot.
        return "retry"


def process_one_source_research() -> str:
    """Perform one bounded start or refresh operation for the worker loop."""
    started = start_one_source_research()
    if started != "idle":
        return started
    refreshed = refresh_one_source_research()
    if refreshed != "idle":
        return refreshed

    # One completed scan per tick is reconciled for releases that stored
    # proposals before automatic candidate findings existed.
    _worker_context()
    statement = (
        sa.select(SpiderFootScan)
        .where(
            SpiderFootScan.research_action_id.is_not(None),
            SpiderFootScan.is_deleted.is_(False),
            SpiderFootScan.status == "completed",
        )
        .order_by(SpiderFootScan.created_at, SpiderFootScan.id)
        .limit(50)
    )
    if db.session.bind and db.session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    for scan in db.session.execute(statement).scalars():
        action = db.session.get(WorkflowResearchAction, scan.research_action_id)
        if action is None:
            continue
        state = scan.result_summary if isinstance(scan.result_summary, dict) else {}
        proposals = state.get("proposals")
        imported = state.get("imported_indexes", [])
        if not isinstance(proposals, list) or not proposals:
            continue
        if isinstance(imported, list) and all(
            index in imported for index in range(len(proposals))
        ):
            continue
        created = _materialize_completed_proposals(scan, action)
        if created:
            action.result_summary = (
                f"{created} kandidaatbevindingen toegevoegd vanuit verdiept bronnenonderzoek"
            )
        db.session.commit()
        return "materialized" if created else "reconciled"
    db.session.commit()
    return "idle"
