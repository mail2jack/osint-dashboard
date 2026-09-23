"""Static contract tests for the explicit source-research worker installer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_unit_is_disabled_by_default_and_hardened():
    unit = (ROOT / "deploy/osint-source-research-worker.service").read_text(
        encoding="utf-8"
    )
    assert "Environment=WORKFLOW_SOURCE_RESEARCH_WORKER_ENABLED=0" in unit
    assert "EnvironmentFile=-/etc/default/osint-source-research-worker" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "ExecStart=/opt/osint-dashboard/venv/bin/python3" in unit


def test_installer_requires_explicit_enablement_and_verifies_unit():
    script = (ROOT / "scripts/install_source_research_worker.sh").read_text(
        encoding="utf-8"
    )
    assert '"${1:-}" != "--enable"' in script
    assert '"$(id -u)" -ne 0' in script
    assert "systemd-analyze verify" in script
    assert "systemctl enable --now" in script
    assert "WORKFLOW_SOURCE_RESEARCH_WORKER_ENABLED=1" in script


def test_entry_point_never_claims_work_while_disabled():
    worker = (ROOT / "scripts/run_source_research_worker.py").read_text(
        encoding="utf-8"
    )
    assert 'os.environ.get("WORKFLOW_SOURCE_RESEARCH_WORKER_ENABLED") != "1"' in worker
    assert "process_one_source_research" in worker
    assert "poll_seconds < 5" in worker
