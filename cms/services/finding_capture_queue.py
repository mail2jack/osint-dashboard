"""Queue-only capture requests for the dedicated FEAT-1 worker.

This module intentionally never imports Playwright.  It validates ownership
and a strict public target before writing a queue row; a later, separately
deployed worker is the only component allowed to claim and execute it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from cms.models import FindingCaptureJob, db
from cms.services.ssrf_guard import validate_capture_url


class CaptureRequestRejected(ValueError):
    """The target or finding does not satisfy the queue contract."""


class CaptureAlreadyActive(RuntimeError):
    """The database rejected a second active capture request."""


class CaptureStateError(RuntimeError):
    """A worker attempted an invalid queue state transition."""


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


def claim_next_capture_job() -> FindingCaptureJob | None:
    """Atomically claim the oldest queued job for the dedicated worker.

    The worker operates with its own explicitly-established RLS bypass context.
    ``SKIP LOCKED`` prevents a future second worker from waiting on an active
    claim under PostgreSQL; SQLite uses its normal transaction locking in tests.
    """
    statement = (
        sa.select(FindingCaptureJob)
        .where(FindingCaptureJob.status == "queued")
        .order_by(FindingCaptureJob.created_at, FindingCaptureJob.id)
        .limit(1)
    )
    if db.session.bind and db.session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    job = db.session.execute(statement).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.session.flush()
    return job


def complete_capture_job(job: FindingCaptureJob, screenshot_id: str) -> None:
    """Finish a claimed job only after its evidence row was persisted."""
    if job.status != "running":
        raise CaptureStateError("Only a running capture job can complete")
    job.screenshot_id = screenshot_id
    job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    db.session.flush()


def fail_capture_job(job: FindingCaptureJob, error: str = "Capture failed") -> None:
    """Finish a claimed job with a bounded, non-sensitive failure message."""
    if job.status != "running":
        raise CaptureStateError("Only a running capture job can fail")
    job.status = "failed"
    job.error = error[:300]
    job.completed_at = datetime.now(timezone.utc)
    db.session.flush()
