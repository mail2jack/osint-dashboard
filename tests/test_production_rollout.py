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
    assert 'mail -r "$sender"' in source
    assert "server@iveras.com" in source


def test_failed_app_deploy_stops_before_license_deploy_and_dry_run_calls_gate():
    source = (ROOT / "scripts/production_rollout.sh").read_text(encoding="utf-8")
    app_block = source.split('if sudo bash "$APP_DIR/scripts/deploy.sh"; then', 1)[1]
    app_block = app_block.split(
        'if sudo bash "$APP_DIR/license-server/deploy/deploy.sh"', 1
    )[0]
    assert "write_report fail" in app_block
    assert "exit 1" in app_block
    assert 'deploy.sh" --dry-run' in source
    dry_run_block = source.split('if [ "$DRY_RUN" = true ]; then', 1)[1].split(
        'if [ "$CONFIRM" != true ]', 1
    )[0]
    assert "write_report" not in dry_run_block
    assert "|| true" not in source.split("write_report()", 1)[1].split("}", 1)[0]


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


def test_sync_units_script_is_syntax_safe_and_does_not_enable():
    script = ROOT / "scripts/sync_units.sh"
    result = subprocess.run(["bash", "-n", str(script)], check=False)
    assert result.returncode == 0
    source = script.read_text(encoding="utf-8")
    assert "/etc/systemd/system" in source
    assert 'install -o root -g root -m 0644' in source
    assert "systemctl daemon-reload" in source
    assert "systemctl enable" not in source
    assert "systemctl start" not in source
    assert "osint-dashboard.service" in source


def test_update_script_syncs_units_before_deps():
    source = (ROOT / "scripts/update.sh").read_text(encoding="utf-8")
    sync_marker = source.index("scripts/sync_units.sh")
    deps_marker = source.index("Afhankelijkheden installeren")
    assert sync_marker < deps_marker
    assert "daemon-reload" in source or "sync_units.sh" in source
