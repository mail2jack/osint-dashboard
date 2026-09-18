"""One-job orchestration for the dedicated finding-capture worker.

This module is intentionally free of Flask routes and scheduling.  The worker
entry point establishes an application context only after its sandbox preflight
has passed, then calls :func:`process_one_capture_job` once.
"""

from __future__ import annotations

import logging

from cms.models import FindingCaptureJob, db
from cms.services.finding_capture_evidence import persist_capture_evidence
from cms.services.finding_capture_executor import CaptureExecutionError, capture_page_as_png
from cms.services.finding_capture_queue import claim_next_capture_job, fail_capture_job
from cms.tenant_context import set_tenant_context

logger = logging.getLogger(__name__)


def _worker_context() -> None:
    """Set the explicit FORCE-RLS bypass used by this audited background path."""
    set_tenant_context(db, None, bypass_rls=True)


def process_one_capture_job() -> str:
    """Claim and process at most one job, returning ``idle``, ``completed`` or ``failed``.

    The running state is committed before Chromium runs.  If capture or
    persistence fails, the job is reloaded and marked failed in a new
    transaction.  No exception text or target URL is saved on the job because
    they can be sensitive evidence.
    """
    _worker_context()
    job = claim_next_capture_job()
    if job is None:
        db.session.commit()
        return "idle"
    job_id = job.id
    target_url = job.target_url
    db.session.commit()

    try:
        captured = capture_page_as_png(target_url)
        _worker_context()
        running_job = db.session.get(FindingCaptureJob, job_id)
        if running_job is None or running_job.status != "running":
            raise CaptureExecutionError("Capture job state changed unexpectedly")
        persist_capture_evidence(running_job, captured)
        return "completed"
    except Exception:
        db.session.rollback()
        try:
            _worker_context()
            running_job = db.session.get(FindingCaptureJob, job_id)
            if running_job is not None and running_job.status == "running":
                fail_capture_job(running_job, "Capture worker could not complete evidence")
                db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Finding capture worker could not record a failed job")
        logger.exception("Finding capture worker failed job_id=%s", job_id)
        return "failed"
