"""Regression: update.sh helper functions (source-friendly, no server).

update.sh defines run_migration_step() and record_deployed_sha() as
sourcing-friendly functions guarded by a BASH_SOURCE check. This module
loads the script with `source` (skipping the main deploy flow) and exercises
the helpers with stubs so no server, systemd or database is touched.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    stub = tmp_path / f"fake_python_{exit_code}"
    stub.write_text(f"#!/bin/bash\nexit {exit_code}\n")
    stub.chmod(0o755)
    return stub


def test_update_sh_aborts_on_failing_migration_step(tmp_path):
    """A failing migration step must stop the deploy with exit code 1."""
    stub = _stub_python(tmp_path, 42)
    result = _run_bash(
        "source update.sh; run_migration_step",
        ROOT,
        {"PYTHON_BIN": str(stub), "PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 1
    assert "Database migration step failed" in result.stderr


def test_update_sh_migration_step_succeeds_with_ok_python(tmp_path):
    stub = _stub_python(tmp_path, 0)
    result = _run_bash(
        "source update.sh; run_migration_step",
        ROOT,
        {"PYTHON_BIN": str(stub), "PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert "Migrations applied" in result.stdout


def test_update_sh_records_deployed_sha(tmp_path):
    head = _init_git_repo(tmp_path)

    result = _run_bash(
        "source update.sh; record_deployed_sha",
        ROOT,
        {"PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert "Deployed commit recorded" in result.stdout
    assert (tmp_path / ".deployed_sha").read_text().strip() == head


def test_update_sh_record_deployed_sha_not_git_repo(tmp_path):
    result = _run_bash(
        "source update.sh; record_deployed_sha",
        ROOT,
        {"PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert "not updated" in result.stdout
    assert not (tmp_path / ".deployed_sha").exists()


def _init_git_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return head