"""Static, sourceability, and behavioral safety tests for the managed
Gunicorn control-socket override installer.

These tests never need root, sudo, systemd, a running service, or a live
control socket. The installer is written to be sourceable (a
``BASH_SOURCE[0] == $0`` guard, mirroring ``scripts/migrate.sh``): the test
harness overrides APP_DIR/DST_BASE to a sandbox with shims and sources the
script, then drives ``run_install``/the guard helpers. The deployed installer
always resolves to /opt/osint-dashboard and /etc/systemd/system and executes
the real venv binary read-only; nothing here starts or restarts a service.
"""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "deploy/osint-dashboard-gunicorn2.override.conf"
INSTALLER = ROOT / "scripts/install_dashboard_gunicorn_override.sh"
RUNBOOK = ROOT / "docs/runbook-gunicorn-control-socket.md"

APP_DIR_DEFAULT = "/opt/osint-dashboard"
SRC_DEFAULT = f"{APP_DIR_DEFAULT}/deploy/osint-dashboard-gunicorn2.override.conf"
SERVICE_CANONICAL = "osint-dashboard.service"
VENV_GUNICORN_DEFAULT = f"{APP_DIR_DEFAULT}/venv/bin/gunicorn"


def _run_bash(script, cwd, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _sandbox(tmp_path, deploy_text=None, dst_text=None, venv_gunicorn_text=None):
    """Build a sandbox APP_DIR with optional SRC/DST/venv binary shims."""
    deploy = tmp_path / "deploy"
    deploy.mkdir(parents=True)
    src_path = deploy / "osint-dashboard-gunicorn2.override.conf"
    src_path.write_text(deploy_text or "ExecStart=managed\n", encoding="utf-8")

    if dst_text is not None:
        dst_dir = tmp_path / "osint-dashboard.service.d"
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / "override.conf").write_text(dst_text, encoding="utf-8")

    if venv_gunicorn_text is not None:
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        (venv_bin / "gunicorn").write_text(venv_gunicorn_text, encoding="utf-8")
        (venv_bin / "gunicorn").chmod(0o755)

    return tmp_path, src_path


LEGACY = "ExecStart=/opt/osint-dashboard/venv/bin/gunicorn --workers 2 " \
    "--worker-class sync --threads 1 --bind 127.0.0.1:5000 --timeout 120 app:app"
REPO_LINE = "ExecStart=/opt/osint-dashboard/venv/bin/gunicorn --workers 2 " \
    "--worker-class sync --threads 1 --bind 127.0.0.1:5000 --timeout 120 " \
    "--no-control-socket app:app"


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


def test_drift_guard_accepts_exact_repo_source(tmp_path):
    sandbox, src_path = _sandbox(tmp_path, deploy_text=REPO_LINE)
    dst_dir = sandbox / "osint-dashboard.service.d"
    dst_dir.mkdir(parents=True)
    (dst_dir / "override.conf").write_text(REPO_LINE, encoding="utf-8")

    result = _run_bash(
        f'source {INSTALLER} && same_override "$SRC" "$DST"',
        sandbox,
        {"APP_DIR": str(sandbox), "DST_BASE": str(sandbox)},
    )
    assert result.returncode == 0, result.stderr


def test_drift_guard_accepts_known_legacy_without_flag(tmp_path):
    sandbox, src_path = _sandbox(tmp_path, deploy_text=REPO_LINE)
    dst_dir = sandbox / "osint-dashboard.service.d"
    dst_dir.mkdir(parents=True)
    (dst_dir / "override.conf").write_text(LEGACY, encoding="utf-8")

    result = _run_bash(
        f'source {INSTALLER} && same_override "$SRC" "$DST"',
        sandbox,
        {"APP_DIR": str(sandbox), "DST_BASE": str(sandbox)},
    )
    assert result.returncode == 0, result.stderr


def test_drift_guard_refuses_unexpected_extra_rule_without_write(tmp_path):
    sandbox, src_path = _sandbox(tmp_path, deploy_text=REPO_LINE)
    unexpected = REPO_LINE + " ExtraRule=whatever"
    dst_dir = sandbox / "osint-dashboard.service.d"
    dst_dir.mkdir(parents=True)
    dst_path = dst_dir / "override.conf"
    dst_path.write_text(unexpected, encoding="utf-8")

    result = _run_bash(
        "source {} && same_override \"$SRC\" \"$DST\"".format(INSTALLER),
        sandbox,
        {"APP_DIR": str(sandbox), "DST_BASE": str(sandbox)},
    )
    assert result.returncode != 0
    assert dst_path.read_text(encoding="utf-8") == unexpected


def test_installer_refuses_real_venv_without_flag_readonly(tmp_path):
    sandbox, src_path = _sandbox(
        tmp_path,
        deploy_text=REPO_LINE,
        venv_gunicorn_text=(
            "#!/usr/bin/env bash\n"
            "echo 'usage: gunicorn [OPTIONS] [APP_MODULE]'\n"
            "echo '  --worker-class sync'\n"
            "echo '  --threads 1'\n"
            "echo '  --no-control-socket   Disable control socket. [False]'\n"
        ),
    )
    result = _run_bash(
        "source {} && gunicorn_supports_flag".format(INSTALLER),
        sandbox,
        {"APP_DIR": str(sandbox)},
    )
    assert result.returncode == 0, result.stderr


def test_installer_refuses_real_venv_when_flag_absent(tmp_path):
    sandbox, src_path = _sandbox(
        tmp_path,
        deploy_text=REPO_LINE,
        venv_gunicorn_text=(
            "#!/usr/bin/env bash\n"
            "echo 'usage: gunicorn [OPTIONS] [APP_MODULE]'\n"
            "echo '  --worker-class sync'\n"
        ),
    )
    result = _run_bash(
        "source {} && gunicorn_supports_flag".format(INSTALLER),
        sandbox,
        {"APP_DIR": str(sandbox)},
    )
    assert result.returncode != 0
    assert "does not support --no-control-socket" in result.stderr


def test_installer_refuses_when_real_venv_binary_missing(tmp_path):
    sandbox, src_path = _sandbox(tmp_path, deploy_text=REPO_LINE)
    result = _run_bash(
        "source {} && gunicorn_supports_flag".format(INSTALLER),
        sandbox,
        {"APP_DIR": str(sandbox)},
    )
    assert result.returncode != 0
    assert "not executable" in result.stderr or "not found" in result.stderr


def test_installer_refuses_when_real_venv_help_fails(tmp_path):
    sandbox, src_path = _sandbox(
        tmp_path,
        deploy_text=REPO_LINE,
        venv_gunicorn_text="#!/usr/bin/env bash\nexit 1\n",
    )
    result = _run_bash(
        "source {} && gunicorn_supports_flag".format(INSTALLER),
        sandbox,
        {"APP_DIR": str(sandbox)},
    )
    assert result.returncode != 0
    assert "abort" in result.stderr.lower()


def test_installer_env_defaults_always_resolve_to_fixed_paths():
    source = INSTALLER.read_text(encoding="utf-8")
    assert 'APP_DIR="${APP_DIR:-/opt/osint-dashboard}"' in source
    assert 'DST_BASE="${DST_BASE:-/etc/systemd/system}"' in source
    assert f'SERVICE="{SERVICE_CANONICAL}"' in source
    assert 'GUNICORN="$APP_DIR/venv/bin/gunicorn"' in source


def test_direct_execute_refuses_deviant_paths_before_any_change(tmp_path):
    """Direct (deployed) execution is fail-closed on deviant APP_DIR/DST_BASE:
    it fails before the root check, any file access, gunicorn --help, backup,
    write, daemon-reload, or systemd-analyze — nothing is written anywhere."""
    deviant_app = tmp_path / "deviant-app"
    deviant_app.mkdir(parents=True)
    deploy = deviant_app / "deploy"
    deploy.mkdir()
    (deploy / "osint-dashboard-gunicorn2.override.conf").write_text(
        "ExecStart=/opt/osint-dashboard/venv/bin/gunicorn --workers 2 "
        "--worker-class sync --threads 1 --bind 127.0.0.1:5000 --timeout 120 "
        "--no-control-socket app:app\n",
        encoding="utf-8",
    )
    venv_bin = deviant_app / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "gunicorn"
    shim.write_text(
        "#!/usr/bin/env bash\ntouch \"$APP_DIR/ran.txt\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = _run_bash(
        "bash {}".format(str(INSTALLER)),
        tmp_path,
        {"APP_DIR": str(deviant_app), "DST_BASE": str(deviant_app)},
    )
    assert result.returncode != 0
    assert "must be /opt/osint-dashboard" in result.stderr
    assert "run as root" not in result.stderr
    assert not (deviant_app / "ran.txt").exists()          # gunicorn never invoked
    assert not list(tmp_path.rglob("override.conf"))       # nothing written
    assert not list(tmp_path.rglob("*override.conf.backup*"))


def test_runbook_documents_explicit_install_restart_and_rollback():
    source = RUNBOOK.read_text(encoding="utf-8")
    assert "update.sh" in source
    assert "sync_units.sh" in source
    assert "install_dashboard_gunicorn_override.sh" in source
    assert "systemctl restart osint-dashboard" in source
    assert "backup-<UTC>" in source
    assert "No application code, database migration" in source
