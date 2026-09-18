"""Atomic persistence of sandboxed FEAT-1 capture evidence.

Only the dedicated worker may call this after it has safely captured a page.
The web process never imports this module.  Database rows, queue completion and
the audit record commit together; an uncommitted screenshot file is removed on
every failure path.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import current_app

from cms.models import AuditLog, Finding, FindingCaptureJob, FindingScreenshot, db
from cms.services.finding_capture_executor import CapturedPage
from cms.services.finding_capture_queue import complete_capture_job


class CaptureEvidenceError(RuntimeError):
    """Captured bytes cannot safely be retained as evidence."""


def _evidence_root() -> Path:
    return (Path(current_app.instance_path) / "finding_screenshots").resolve()


def _write_png_atomically(destination: Path, png_bytes: bytes) -> None:
    """Write one private evidence file without exposing a partial image."""
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(png_bytes)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CaptureEvidenceError("Could not write captured evidence") from exc


def persist_capture_evidence(job: FindingCaptureJob, captured: CapturedPage) -> FindingScreenshot:
    """Persist one bounded PNG and complete its running queue job atomically."""
    if job.status != "running":
        raise CaptureEvidenceError("Only a running capture job can persist evidence")
    if not captured.png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CaptureEvidenceError("Capture output is not a PNG")
    finding = db.session.get(Finding, job.finding_id)
    if (
        finding is None
        or finding.case_id != job.case_id
        or finding.tenant_id != job.tenant_id
    ):
        raise CaptureEvidenceError("Capture job no longer matches its finding")

    filename = f"{uuid.uuid4()}.png"
    destination = (_evidence_root() / finding.id / filename).resolve()
    try:
        destination.relative_to(_evidence_root() / finding.id)
    except ValueError as exc:  # defensive: filename is generated above
        raise CaptureEvidenceError("Invalid evidence destination") from exc

    _write_png_atomically(destination, captured.png_bytes)
    screenshot = FindingScreenshot(
        id=str(uuid.uuid4()),
        tenant_id=job.tenant_id,
        finding_id=finding.id,
        url=f"/cms/workflow/uploads/{finding.id}/{filename}",
        source_url=captured.source_url,
        file_path=str(destination),
        file_size=len(captured.png_bytes),
        capture_provenance={
            "kind": "sandboxed_browser_capture",
            "capture_job_id": job.id,
            "page_title": captured.title,
        },
        notes="Captured by dedicated evidence worker",
        created_by=job.requested_by_id,
    )
    try:
        db.session.add(screenshot)
        complete_capture_job(job, screenshot.id)
        AuditLog.log(
            user_id=job.requested_by_id,
            action="create",
            entity_type="finding_screenshot",
            entity_id=screenshot.id,
            case_id=job.case_id,
            tenant_id=job.tenant_id,
            new_values={
                "kind": "sandboxed_browser_capture",
                "source_url": captured.source_url,
                "file_size": len(captured.png_bytes),
            },
            description="Dedicated worker captured finding screenshot evidence",
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        destination.unlink(missing_ok=True)
        raise CaptureEvidenceError("Could not persist captured evidence") from exc
    return screenshot
