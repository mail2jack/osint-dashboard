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


def test_update_sh_self_restarts_after_pull_updates_script(tmp_path):
    """A pull that updates update.sh must self-restart and run the NEW script
    version: pull_latest_and_maybe_self_restart() re-execs the pulled script
    path, and only the pulled version prints the marker. Stale (buffered)
    code would print the old marker instead."""
    stale_marker = "UPDATE_SCRIPT_STALE_MARKER"
    new_marker = "UPDATE_SCRIPT_NEW_MARKER"

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=master", str(origin)],
        check=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git_config(clone)

    (clone / "a.txt").write_text("a")
    subprocess.run(["git", "add", "a.txt"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "master"], cwd=clone, check=True)

    (clone / "update.sh").write_text(f"#!/bin/bash\necho {stale_marker}\n")
    subprocess.run(["git", "add", "update.sh"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "v1 updater"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "master"], cwd=clone, check=True)
    v1_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Newer update.sh on the remote, clone stays on the older commit.
    (clone / "update.sh").write_text(f"#!/bin/bash\necho {new_marker}\n")
    subprocess.run(["git", "add", "update.sh"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "v2 updater"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "master"], cwd=clone, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", v1_sha], cwd=clone, check=True)

    runner = tmp_path / "runner.sh"
    runner.write_text(
        "#!/bin/bash\n"
        "source \"${UPDATE_SH_PATH}\"\n"
        "pull_latest_and_maybe_self_restart \"${REEXEC_SCRIPT}\"\n"
        "echo NO_RESTART_UNEXPECTED\n"
        "exit 3\n"
    )
    runner.chmod(0o755)
    env = {
        **os.environ,
        "UPDATE_SH_PATH": str(ROOT / "update.sh"),
        "PROJECT_DIR": str(clone),
        "CURRENT_BRANCH": "master",
        "REEXEC_SCRIPT": str(clone / "update.sh"),
    }
    result = subprocess.run(
        ["bash", str(runner)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert new_marker in result.stdout, result.stdout
    assert stale_marker not in result.stdout, result.stdout
    assert "Deploy script updated" in result.stdout, result.stdout
    assert "NO_RESTART_UNEXPECTED" not in result.stdout
    assert new_marker in (clone / "update.sh").read_text()


def _git_config(repo: Path) -> None:
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo, check=True)


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