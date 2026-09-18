from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from cms.models import AuditLog, Case, Client, Finding, FindingCaptureJob, User, db
from cms.services.finding_capture_evidence import (
    CaptureEvidenceError,
    persist_capture_evidence,
)
from cms.services.finding_capture_executor import CapturedPage


PNG = b"\x89PNG\r\n\x1a\n" + b"test-pixels"


def _running_job():
    user = User.query.filter_by(username="admin").first()
    client = Client(name="Capture Evidence Client")
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number="CAPTURE-EVIDENCE",
        client_id=client.id,
        title="Capture evidence",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    finding = Finding(
        case_id=case.id,
        tenant_id=case.tenant_id,
        title="Capture target",
        content="test",
        source_type="manual",
        created_by=user.id,
    )
    db.session.add(finding)
    db.session.flush()
    job = FindingCaptureJob(
        tenant_id=case.tenant_id,
        case_id=case.id,
        finding_id=finding.id,
        requested_by_id=user.id,
        target_url="https://fixture.example/capture",
        status="running",
    )
    db.session.add(job)
    db.session.commit()
    return job, finding


def test_persisted_capture_is_private_evidence_with_source_and_audit(
    app, db_session, tmp_path, monkeypatch
):
    job, finding = _running_job()
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    long_source = "https://fixture.example/" + "a" * 1900
    screenshot = persist_capture_evidence(
        job, CapturedPage(PNG, long_source, "Fixture title")
    )

    assert screenshot.source_url == long_source
    assert screenshot.file_size == len(PNG)
    assert screenshot.capture_provenance["capture_job_id"] == job.id
    assert screenshot.url.startswith(f"/cms/workflow/uploads/{finding.id}/")
    assert (tmp_path / "finding_screenshots" / finding.id).exists()
    assert job.status == "completed"
    assert job.screenshot_id == screenshot.id
    assert AuditLog.query.filter_by(entity_id=screenshot.id, action="create").count() == 1


def test_persistence_failure_removes_evidence_file(
    app, db_session, tmp_path, monkeypatch
):
    job, finding = _running_job()
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    with patch("cms.services.finding_capture_evidence.AuditLog.log", side_effect=RuntimeError):
        with pytest.raises(CaptureEvidenceError):
            persist_capture_evidence(
                job, CapturedPage(PNG, "https://fixture.example/", "Fixture")
            )

    evidence_dir = tmp_path / "finding_screenshots" / finding.id
    assert not list(evidence_dir.glob("*.png"))
    assert AuditLog.query.filter_by(entity_type="finding_screenshot").count() == 0
