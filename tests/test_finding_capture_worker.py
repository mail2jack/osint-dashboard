from types import SimpleNamespace
from unittest.mock import Mock, patch

from cms.services.finding_capture_worker import process_one_capture_job


def _session():
    return SimpleNamespace(commit=Mock(), rollback=Mock(), get=Mock())


def test_worker_returns_idle_without_starting_browser():
    session = _session()
    with patch("cms.services.finding_capture_worker.db", SimpleNamespace(session=session)), patch(
        "cms.services.finding_capture_worker._worker_context"
    ), patch(
        "cms.services.finding_capture_worker.claim_next_capture_job", return_value=None
    ), patch("cms.services.finding_capture_worker.capture_page_as_png") as capture:
        assert process_one_capture_job() == "idle"
    session.commit.assert_called_once()
    capture.assert_not_called()


def test_worker_completes_one_claimed_job():
    session = _session()
    claimed = SimpleNamespace(id="job-1", target_url="https://fixture.example", status="running")
    running = SimpleNamespace(id="job-1", status="running")
    session.get.return_value = running
    artifact = object()
    with patch("cms.services.finding_capture_worker.db", SimpleNamespace(session=session)), patch(
        "cms.services.finding_capture_worker._worker_context"
    ), patch(
        "cms.services.finding_capture_worker.claim_next_capture_job", return_value=claimed
    ), patch(
        "cms.services.finding_capture_worker.capture_page_as_png", return_value=artifact
    ), patch("cms.services.finding_capture_worker.persist_capture_evidence") as persist:
        assert process_one_capture_job() == "completed"
    persist.assert_called_once_with(running, artifact)


def test_worker_marks_claimed_job_failed_without_sensitive_error():
    session = _session()
    claimed = SimpleNamespace(id="job-2", target_url="https://fixture.example", status="running")
    running = SimpleNamespace(id="job-2", status="running")
    session.get.return_value = running
    with patch("cms.services.finding_capture_worker.db", SimpleNamespace(session=session)), patch(
        "cms.services.finding_capture_worker._worker_context"
    ), patch(
        "cms.services.finding_capture_worker.claim_next_capture_job", return_value=claimed
    ), patch(
        "cms.services.finding_capture_worker.capture_page_as_png", side_effect=RuntimeError
    ), patch("cms.services.finding_capture_worker.fail_capture_job") as fail:
        assert process_one_capture_job() == "failed"
    fail.assert_called_once_with(running, "Capture worker could not complete evidence")
    assert session.commit.call_count == 2
