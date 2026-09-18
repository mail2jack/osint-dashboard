from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_capture_timer_is_fast_but_does_not_replay_missed_runs():
    timer = (ROOT / "deploy/osint-finding-capture-worker.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=15s" in timer
    assert "AccuracySec=1s" in timer
    assert "Persistent=false" in timer
    assert "Unit=osint-finding-capture-worker.service" in timer


def test_capture_timer_activation_is_explicit_and_preserves_sandbox_config():
    script = (ROOT / "scripts/install_finding_capture_timer.sh").read_text(encoding="utf-8")
    assert '"${1:-}" != "--enable"' in script
    assert "FINDING_CAPTURE_CHROMIUM_PATH" in script
    assert "FINDING_CAPTURE_WORKER_ENABLED=1" in script
    assert "systemd-analyze verify" in script
    assert "enable --now" in script
    assert "systemctl restart" not in script


def test_worker_checks_for_work_before_launching_chromium():
    worker = (ROOT / "scripts/run_finding_capture_worker.py").read_text(encoding="utf-8")
    assert "has_queued_capture_job" in worker
    assert worker.index("has_queued_capture_job()") < worker.rindex("probe_sandbox(")
