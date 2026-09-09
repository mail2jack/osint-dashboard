"""Regression: scripts/migrate.sh sourceable helpers (no server/sudo).

scripts/migrate.sh defines run_migrate() as a source-friendly function guarded
by a BASH_SOURCE check. This module loads the script with `source` (skipping
the main production flow) and exercises run_migrate() with a stub python so
no sudo, systemd or database is touched.

P1 (incident 20260909): set -euo pipefail + explicit error check ensure that
a failing `alembic upgrade head` aborts the deploy (fail-closed) instead of
silently printing "migrate.sh klaar" and exiting 0.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATE_SH = ROOT / "scripts" / "migrate.sh"


def _run_bash(script: str, cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def _stub_python(tmp_path: Path, exit_code: int) -> Path:
    stub = tmp_path / f"stub_alembic_{exit_code}"
    stub.write_text(f"#!/bin/bash\nexit {exit_code}\n")
    stub.chmod(0o755)
    return stub


def _setup_app_dir(tmp_path: Path, env_url: str = "sqlite:///test.db") -> Path:
    """Create a minimal app dir with .env and a fake venv python."""
    app_dir = tmp_path / "app"
    venv_bin = app_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = _stub_python(tmp_path, 0)
    link = venv_bin / "python3"
    link.symlink_to(stub)
    (app_dir / ".env").write_text(f"DATABASE_URL={env_url}\n")
    return app_dir


def test_migrate_sh_aborts_on_failing_alembic(tmp_path):
    """A failing `alembic upgrade head` must abort with rc != 0 (P1)."""
    app_dir = _setup_app_dir(tmp_path)
    # Replace the stub python with one that always fails.
    stub = _stub_python(tmp_path, 42)
    result = _run_bash(
        f"source {MIGRATE_SH} && run_migrate",
        ROOT,
        {"APP_DIR": str(app_dir), "PYTHON_BIN": str(stub)},
    )
    assert result.returncode != 0, (
        f"run_migrate moest falen maar gaf rc=0:\n{result.stdout}\n{result.stderr}"
    )
    assert "alembic upgrade head mislukt" in result.stderr, (
        f"verwachte foutmelding ontbreekt:\n{result.stderr}"
    )


def test_migrate_sh_succeeds_on_ok_alembic(tmp_path):
    """A succeeding `alembic upgrade head` must return rc 0."""
    app_dir = _setup_app_dir(tmp_path)
    stub = _stub_python(tmp_path, 0)
    result = _run_bash(
        f"source {MIGRATE_SH} && run_migrate",
        ROOT,
        {"APP_DIR": str(app_dir), "PYTHON_BIN": str(stub)},
    )
    assert result.returncode == 0, (
        f"run_migrate moest slagen maar gaf rc={result.returncode}:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_migrate_sh_aborts_when_env_file_missing(tmp_path):
    """Zonder .env moet run_migrate direct falen."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "venv" / "bin").mkdir(parents=True)
    stub = _stub_python(tmp_path, 0)
    result = _run_bash(
        f"source {MIGRATE_SH} && run_migrate",
        ROOT,
        {"APP_DIR": str(app_dir), "PYTHON_BIN": str(stub)},
    )
    assert result.returncode != 0
    assert "ontbreekt" in result.stderr


def test_migrate_sh_exports_database_url_from_env(tmp_path):
    """DATABASE_URL uit .env moet worden doorgegeven aan de stub."""
    app_dir = tmp_path / "app"
    venv_bin = app_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    db_url = "postgresql://test:test@localhost/testdb"
    (app_dir / ".env").write_text(f"DATABASE_URL={db_url}\n")
    # Python stub print DATABASE_URL zodat we kunnen verifiëren dat die werd gezet.
    stub = tmp_path / "stub_print_env"
    stub.write_text("#!/bin/bash\necho URL=$DATABASE_URL\n")
    stub.chmod(0o755)
    result = _run_bash(
        f"source {MIGRATE_SH} && run_migrate",
        ROOT,
        {"APP_DIR": str(app_dir), "PYTHON_BIN": str(stub)},
    )
    assert result.returncode == 0, f"run_migrate faalde:\n{result.stderr}"
    assert f"URL={db_url}" in result.stdout, (
        f"DATABASE_URL niet doorgegeven:\n{result.stdout}"
    )


def test_migrate_sh_fallback_without_database_url(tmp_path):
    """Zonder DATABASE_URL in .env moet SQLite fallback worden gebruikt."""
    app_dir = tmp_path / "app"
    venv_bin = app_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (app_dir / ".env").write_text("# lege .env zonder DATABASE_URL\n")
    stub = tmp_path / "stub_noenv"
    stub.write_text('#!/bin/bash\necho "URL_SET=${DATABASE_URL:+yes}"\n')
    stub.chmod(0o755)
    result = _run_bash(
        f"source {MIGRATE_SH} && run_migrate",
        ROOT,
        {"APP_DIR": str(app_dir), "PYTHON_BIN": str(stub)},
    )
    assert result.returncode == 0, f"run_migrate faalde:\n{result.stderr}"
    assert "WARNING: geen DATABASE_URL" in (result.stdout + result.stderr)
    assert "URL_SET=" in result.stdout, (
        f"DATABASE_URL moest leeg zijn:\n{result.stdout}"
    )
