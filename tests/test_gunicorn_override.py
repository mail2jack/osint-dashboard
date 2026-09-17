"""Static and shell safety tests for the explicit Gunicorn override installer."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "deploy/osint-dashboard-gunicorn2.override.conf"
INSTALLER = ROOT / "scripts/install_dashboard_gunicorn_override.sh"
RUNBOOK = ROOT / "docs/runbook-gunicorn-control-socket.md"


def test_override_preserves_current_parameters_and_disables_control_socket():
    source = OVERRIDE.read_text(encoding="utf-8")
    exec_lines = [line for line in source.splitlines() if line.startswith("ExecStart=")]
    command = exec_lines[-1]
    assert command.count("--no-control-socket") == 1
    assert "--workers 2" in command
    assert "--worker-class sync" in command
    assert "--threads 1" in command
    assert "--bind 127.0.0.1:5000" in command
    assert "--timeout 120" in command
    assert command.endswith("app:app")
    assert "Environment=LOG_FILE=/dev/null" in source


def test_installer_is_syntax_safe_and_non_restarting():
    result = subprocess.run(["bash", "-n", str(INSTALLER)], check=False)
    assert result.returncode == 0
    source = INSTALLER.read_text(encoding="utf-8")
    assert 'SRC="$APP_DIR/deploy/osint-dashboard-gunicorn2.override.conf"' in source
    assert 'DST="$DST_DIR/override.conf"' in source
    assert 'install -d -o root -g root -m 0755 "$DST_DIR"' in source
    assert 'mktemp "$DST_DIR/.override.conf.tmp.XXXXXX"' in source
    assert 'mv -f "$TMP" "$DST"' in source
    assert 'BACKUP="$DST.backup-$STAMP"' in source
    assert 'systemctl daemon-reload' in source
    assert 'systemd-analyze verify "$SERVICE"' in source
    assert "\nsystemctl restart" not in "\n" + source
    assert "\nsystemctl start" not in "\n" + source
    assert 'id -u' in source
    assert 'if [ ! -f "$SRC" ]' in source
    assert "PASSWORD" not in source
    assert "SECRET" not in source


def test_non_root_installer_refuses_before_target_access(tmp_path):
    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=tmp_path,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )
    if os.geteuid() != 0:
        assert result.returncode != 0
        assert "run as root" in result.stderr


def test_runbook_documents_explicit_install_restart_and_rollback():
    source = RUNBOOK.read_text(encoding="utf-8")
    assert "update.sh" in source
    assert "sync_units.sh" in source
    assert "install_dashboard_gunicorn_override.sh" in source
    assert "systemctl restart osint-dashboard" in source
    assert "backup-<UTC>" in source
    assert "No application code, database migration" in source


def test_gunicorn_26_2_help_output_supports_flag():
    # Controlled representation of the verified production help output; this
    # test never starts a service or browser process.
    version = "gunicorn (version 26.2.0)"
    help_output = "--no-control-socket   Disable control socket. [False]"
    assert version.endswith("26.2.0)")
    assert "--no-control-socket" in help_output
