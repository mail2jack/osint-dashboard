"""Tests for the guided production rollout wrapper."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rollout_script_syntax_and_safety_controls():
    script = ROOT / "scripts/production_rollout.sh"
    result = subprocess.run(["bash", "-n", str(script)], check=False)
    assert result.returncode == 0
    source = script.read_text(encoding="utf-8")
    assert "--dry-run" in source
    assert "DEPLOY-MASTER" in source
    assert "no automatic rollback" in source
    assert "privacy-purge.timer" in source


def test_failed_app_deploy_stops_before_license_deploy_and_dry_run_calls_gate():
    source = (ROOT / "scripts/production_rollout.sh").read_text(encoding="utf-8")
    app_block = source.split('if sudo bash "$APP_DIR/scripts/deploy.sh"; then', 1)[1]
    app_block = app_block.split(
        'if sudo bash "$APP_DIR/license-server/deploy/deploy.sh"', 1
    )[0]
    assert "write_report fail" in app_block
    assert "exit 1" in app_block
    assert 'deploy.sh" --dry-run' in source


def test_rollout_report_schema(tmp_path):
    checks = tmp_path / "checks.tsv"
    checks.write_text("health\tpass\thealthy\n", encoding="utf-8")
    output = tmp_path / "rollout.json"
    result = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts/rollout_report.py"),
            "--output",
            str(output),
            "--commit-sha",
            "abc123",
            "--checks-file",
            str(checks),
            "--status",
            "pass",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert '"status": "pass"' in output.read_text(encoding="utf-8")
