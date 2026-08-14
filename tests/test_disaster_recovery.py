"""Tests for disaster-recovery scripts and their machine-readable reports."""

import json
import subprocess
from pathlib import Path

from scripts.dr_report import write_report

ROOT = Path(__file__).resolve().parents[1]


def test_dr_report_schema_and_pass_status(tmp_path):
    output = tmp_path / "dr.json"
    report = write_report(
        output,
        backup_id="iveras_backup_20260814_020000",
        commit_sha="abc123",
        checks={"database_restore": {"status": "pass", "detail": "isolated"}},
        counts={"tenants": 2, "cases": 4, "users": 3},
    )

    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded == report
    assert loaded["schema_version"] == 1
    assert loaded["status"] == "pass"
    assert loaded["backup_id"] == "iveras_backup_20260814_020000"
    assert loaded["commit_sha"] == "abc123"
    assert loaded["counts"] == {"tenants": 2, "cases": 4, "users": 3}
    assert "password" not in output.read_text(encoding="utf-8").lower()


def test_dr_report_failure_status(tmp_path):
    output = tmp_path / "dr.json"
    report = write_report(
        output,
        backup_id="backup",
        commit_sha="abc123",
        checks={"uploads": {"status": "fail", "detail": "missing"}},
        counts={},
    )
    assert report["status"] == "fail"


def test_backup_verifier_and_alert_scripts_parse():
    for script in (
        "scripts/verify_backup.sh",
        "scripts/backup_verification_alert.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    drill = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts/dr_drill.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert drill.returncode == 0, drill.stderr


def test_verifier_does_not_restore_to_production():
    source = (ROOT / "scripts/verify_backup.sh").read_text(encoding="utf-8")
    assert "DR_VERIFY_DATABASE_URL" in source
    assert "--admin-url" not in source
    assert "DROP DATABASE" not in source
    assert "UPLOAD_ENTRY=" in source
    assert 'tar tzf "$EXTRACT_DIR/uploads.tar.gz"' in source
    assert "!~ /\\/$/" in source
    assert "restore.sh" not in source


def test_periodic_units_are_present_and_failure_alert_is_wired():
    service = (ROOT / "deploy/osint-backup-verify.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy/osint-backup-verify.timer").read_text(encoding="utf-8")
    alert = (ROOT / "deploy/osint-backup-verify-alert.service").read_text(
        encoding="utf-8"
    )
    assert "OnFailure=osint-backup-verify-alert.service" in service
    assert "OnCalendar=" in timer
    assert "backup_verification_alert.sh" in alert


def test_drill_requires_human_safety_controls():
    source = (ROOT / "scripts/dr_drill.sh").read_text(encoding="utf-8")
    assert "--confirm" in source
    assert "--production-unchanged" in source
    assert "WRONG_KEY_STATUS" in source
    assert "DATABASE_URL" in source


def test_postgres_restore_uses_psql_and_drill_forwards_report_directory():
    helper = (ROOT / "scripts/dr_postgres.py").read_text(encoding="utf-8")
    drill = (ROOT / "scripts/dr_drill.sh").read_text(encoding="utf-8")
    assert '["psql", "-v", "ON_ERROR_STOP=1"' in helper
    assert "subprocess.run(" in helper
    assert "cursor.execute(Path" not in helper
    assert 'DR_REPORT_DIR="$REAL_REPORT_DIR"' in drill
