"""Tests for disaster-recovery scripts and their machine-readable reports."""

import json
import subprocess
from pathlib import Path

from scripts import dr_production_snapshot
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


def test_uploads_listing_pipeline_ignores_sigpipe(tmp_path):
    import tarfile

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    (uploads_dir / "first.txt").write_text("x", encoding="utf-8")
    for index in range(200):
        (uploads_dir / f"blob_{index}.bin").write_bytes(b"y" * 65536)
    archive = tmp_path / "uploads.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(uploads_dir, arcname="uploads")

    script = (ROOT / "scripts/verify_backup.sh").read_text(encoding="utf-8")
    pipeline = (
        'tar tzf "$EXTRACT_DIR/uploads.tar.gz" 2>/dev/null | '
        "awk 'NF && $0 !~ /\\/$/ { print; exit }'"
    )
    command = (
        "set -o pipefail; EXTRACT_DIR={dir}; ENTRY=$({pipeline} || true); "
        'test -n "$ENTRY"; echo "entry=$ENTRY rc=$?"'
    ).format(dir=tmp_path, pipeline=pipeline)
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip().startswith("entry=uploads/") and "rc=0" in result.stdout
    )
    assert "|| true" in script


def test_counts_json_expansion_does_not_double_close_brace():
    result = subprocess.run(
        [
            "bash",
            "-c",
            'COUNTS_JSON=\'{"tenants":3,"cases":7,"users":5}\'; '
            'printf "%s" "$COUNTS_JSON"',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout == '{"tenants":3,"cases":7,"users":5}'

    source = (ROOT / "scripts/verify_backup.sh").read_text(encoding="utf-8")
    assert 'counts-json "$COUNTS_JSON"' in source
    assert 'counts-json "${COUNTS_JSON:-{}}"' not in source
    assert "COUNTS_JSON='{}'" in source


def test_backup_verifier_and_alert_scripts_parse():
    for script in (
        "scripts/verify_backup.sh",
        "scripts/backup_verification_alert.sh",
        "scripts/dr_production_gate.sh",
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

    snapshot = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts/dr_production_gate.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert snapshot.returncode == 0, snapshot.stderr


def test_verifier_does_not_restore_to_production():
    source = (ROOT / "scripts/verify_backup.sh").read_text(encoding="utf-8")
    assert "DR_VERIFY_DATABASE_URL" in source
    assert "--admin-url" not in source
    assert "DROP DATABASE" not in source
    assert "UPLOAD_ENTRY=" in source
    assert 'tar tzf "$EXTRACT_DIR/uploads.tar.gz"' in source
    assert "!~ /\\/$/" in source
    assert "venv/bin/python3" in source
    assert "restore.sh" not in source


def test_verifier_auto_sources_dr_env_default():
    """A bare verify_backup.sh invocation must stay green by sourcing the DR env."""
    source = (ROOT / "scripts/verify_backup.sh").read_text(encoding="utf-8")
    assert "/etc/default/osint-dr" in source
    assert ". /etc/default/osint-dr" in source
    assert "PGSERVICE:-" in source
    assert "DR_VERIFY_DATABASE_URL:-" in source
    assert '[ -f /etc/default/osint-dr ]' in source


def test_periodic_units_are_present_and_failure_alert_is_wired():
    service = (ROOT / "deploy/osint-backup-verify.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy/osint-backup-verify.timer").read_text(encoding="utf-8")
    alert = (ROOT / "deploy/osint-backup-verify-alert.service").read_text(
        encoding="utf-8"
    )
    assert "OnFailure=osint-backup-verify-alert.service" in service
    assert "OnCalendar=" in timer
    assert "backup_verification_alert.sh" in alert


def test_light_health_monitor_uses_incremental_journal_cursor():
    source = (ROOT / "scripts/monitor_health_light.sh").read_text(encoding="utf-8")
    service = (ROOT / "deploy/osint-health-monitor.service").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/install_health_monitor.sh").read_text(encoding="utf-8")
    assert "--after-cursor" in source
    assert "--since" not in source
    assert "-n 1 --show-cursor" in source
    assert "journal.cursor" in source
    assert "health-light.csv" in source
    assert "Restart=always" in service
    assert "WantedBy=multi-user.target" in service


def test_managed_runtime_limits_match_production_canary():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    journald = (ROOT / "deploy/60-osint-journald-limits.conf").read_text(
        encoding="utf-8"
    )
    assert "--workers 2 --worker-class sync --threads 1" in installer
    assert "SystemMaxUse=1G" in journald
    assert "MaxRetentionSec=14day" in journald
    assert "enable --now osint-health-monitor.service" in installer


def test_drill_requires_human_safety_controls():
    source = (ROOT / "scripts/dr_drill.sh").read_text(encoding="utf-8")
    assert "--confirm" in source
    assert "--production-unchanged" in source
    assert "WRONG_KEY_STATUS" in source
    assert "DATABASE_URL" in source


def test_production_gate_requires_second_operator_and_compares_state():
    source = (ROOT / "scripts/dr_production_gate.sh").read_text(encoding="utf-8")
    assert "PRODUCTION-UNCHANGED" in source
    assert "production-before.json" in source
    assert "production-after.json" in source
    assert "same_uploads" in source
    assert "no_temporary_database" in source
    assert "venv/bin/python3" in source
    drill = (ROOT / "scripts/dr_drill.sh").read_text(encoding="utf-8")
    assert "while IFS= read -r candidate" in drill


def test_production_pgreservice_uses_service_database(monkeypatch):
    calls = {}

    def fake_connect(**kwargs):
        calls.update(kwargs)
        return object()

    monkeypatch.delenv("DR_PRODUCTION_DATABASE_URL", raising=False)
    monkeypatch.setenv("DR_PRODUCTION_PGSERVICE", "production-readonly")
    monkeypatch.setattr(dr_production_snapshot.psycopg2, "connect", fake_connect)
    assert dr_production_snapshot._connect() is not None
    assert calls == {"service": "production-readonly"}


def test_schema_fingerprint_uses_sha256():
    source = (ROOT / "scripts/dr_production_snapshot.py").read_text(encoding="utf-8")
    assert "sha256" in source
    assert "md5(" not in source


def test_postgres_restore_uses_psql_and_drill_forwards_report_directory():
    helper = (ROOT / "scripts/dr_postgres.py").read_text(encoding="utf-8")
    drill = (ROOT / "scripts/dr_drill.sh").read_text(encoding="utf-8")
    assert '["psql", "-v", "ON_ERROR_STOP=1"' in helper
    assert "subprocess.run(" in helper
    assert "cursor.execute(Path" not in helper
    assert 'DR_REPORT_DIR="$REAL_REPORT_DIR"' in drill
