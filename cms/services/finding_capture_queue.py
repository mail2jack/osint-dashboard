"""Queue-only capture requests for the dedicated FEAT-1 worker.

This module intentionally never imports Playwright.  It validates ownership
and a strict public target before writing a queue row; a later, separately
deployed worker is the only component allowed to claim and execute it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from cms.models import FindingCaptureJob, db
from cms.services.ssrf_guard import validate_capture_url


class CaptureRequestRejected(ValueError):
    """The target or finding does not satisfy the queue contract."""


class CaptureAlreadyActive(RuntimeError):
    """The database rejected a second active capture request."""


def enqueue_finding_capture(*, case, finding, actor, target_url: str) -> FindingCaptureJob:
    """Create a queued capture request, or fail without a partial row.

    The partial unique indexes are authoritative for both per-tenant and
    global concurrency.  Callers may add an audit record after this function
    returns and commit both changes in their single surrounding transaction.
    """
    if finding.case_id != case.id or finding.tenant_id != case.tenant_id:
        raise CaptureRequestRejected("Finding does not belong to this case")
    if actor.tenant_id != case.tenant_id:
        raise CaptureRequestRejected("Actor does not belong to this tenant")
    valid, reason = validate_capture_url(target_url)
    if not valid:
        raise CaptureRequestRejected(reason)

    job = FindingCaptureJob(
        tenant_id=case.tenant_id,
        case_id=case.id,
        finding_id=finding.id,
        requested_by_id=actor.id,
        target_url=target_url,
        status="queued",
    )
    try:
        db.session.add(job)
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise CaptureAlreadyActive("A capture is already queued or running") from exc
    return job
