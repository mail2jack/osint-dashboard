from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_capture_worker_is_long_lived_and_polls_efficiently():
    unit = (ROOT / "deploy/osint-finding-capture-worker.service").read_text(encoding="utf-8")
    worker = (ROOT / "scripts/run_finding_capture_worker.py").read_text(encoding="utf-8")
    assert "Type=simple" in unit
    assert "--loop" in unit
    assert "FINDING_CAPTURE_WORKER_POLL_SECONDS=15" in unit
    assert "Restart=on-failure" in unit
    assert "time.sleep(poll_seconds)" in worker


def test_capture_timer_activation_is_explicit_and_preserves_sandbox_config():
    script = (ROOT / "scripts/install_finding_capture_timer.sh").read_text(encoding="utf-8")
    assert '"${1:-}" != "--enable"' in script
    assert "FINDING_CAPTURE_CHROMIUM_PATH" in script
    assert "FINDING_CAPTURE_WORKER_ENABLED=1" in script
    assert "systemd-analyze verify" in script
    assert 'enable --now "$SERVICE"' in script
    assert "disable --now \"$LEGACY_TIMER\"" in script


def test_worker_checks_for_work_before_launching_chromium():
    worker = (ROOT / "scripts/run_finding_capture_worker.py").read_text(encoding="utf-8")
    assert "has_queued_capture_job" in worker
    assert worker.index("has_queued_capture_job()") < worker.rindex("probe_sandbox(")
