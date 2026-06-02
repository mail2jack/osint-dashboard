import logging
import os
import re
import time

import flask
from flask import jsonify, current_app, render_template
from flask_login import login_required

from . import cms_bp
from .. import csrf
from ..models import Setting
from ..auth import admin_required

logger = logging.getLogger(__name__)


_SESSION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "flask_session"
)


def _read_session_data(session_id: str) -> dict:
    """Read and deserialize a session file."""
    import json
    import os

    session_file = os.path.join(_SESSION_DIR, f"session_{session_id}")
    try:
        if os.path.exists(session_file):
            with open(session_file) as f:
                raw = f.read()
            data = json.loads(raw)
            return {
                "user_id": data.get("_user_id"),
                "created_at": data.get("_created_at"),
                "ip": data.get("_ip", data.get("ip", "")),
            }
    except Exception:
        pass
    return {}


@cms_bp.route("/admin/sessions", methods=["GET"])
@login_required
@admin_required
def list_sessions() -> dict:
    """List active server-side sessions."""
    accept_html = "text/html" in flask.request.accept_mimetypes.values()
    if accept_html:
        return render_template("cms/sessions.html")

    sessions = []
    if os.path.exists(_SESSION_DIR):
        for fname in sorted(os.listdir(_SESSION_DIR), reverse=True):
            if not fname.startswith("session_"):
                continue
            sid = fname.replace("session_", "")
            data = _read_session_data(sid)
            sessions.append(
                {
                    "session_id": sid,
                    "file": fname,
                    "user_id": data.get("user_id"),
                    "ip": data.get("ip"),
                    "is_current": sid == flask.session.sid
                    if hasattr(flask.session, "sid")
                    else False,
                }
            )
    return jsonify({"sessions": sessions, "count": len(sessions)})


@cms_bp.route("/admin/sessions/<session_id>/delete", methods=["POST"])
@csrf.exempt
@login_required
@admin_required
def delete_session(session_id: str) -> flask.Response:
    """Delete a specific session."""
    import os

    session_file = os.path.join(_SESSION_DIR, f"session_{session_id}")
    if os.path.exists(session_file):
        os.remove(session_file)
        return jsonify({"message": "Session deleted"})
    return jsonify({"error": "Session not found"}), 404


@cms_bp.route("/admin/sessions/delete-all", methods=["POST"])
@csrf.exempt
@login_required
@admin_required
def delete_all_sessions() -> flask.Response:
    """Delete all sessions except the current one."""
    import os

    count = 0
    current_sid = getattr(flask.session, "sid", None)
    if os.path.exists(_SESSION_DIR):
        for fname in os.listdir(_SESSION_DIR):
            if not fname.startswith("session_"):
                continue
            sid = fname.replace("session_", "")
            if sid == current_sid:
                continue
            os.remove(os.path.join(_SESSION_DIR, fname))
            count += 1
    return jsonify({"message": f"Deleted {count} sessions", "deleted": count})


@cms_bp.route("/api/changelog", methods=["GET"])
@login_required
def get_changelog() -> flask.Response:
    """Return the full CHANGELOG.md rendered to simple HTML."""
    import os

    cl_path = os.path.join(current_app.root_path, "CHANGELOG.md")
    if not os.path.exists(cl_path):
        return jsonify({"html": "<p>No changelog available.</p>"})
    with open(cl_path) as f:
        raw = f.read()
    lines = raw.split("\n")
    html_parts = []
    for line in lines:
        if line.startswith("### "):
            html_parts.append(f"<p><strong>{line[4:]}</strong></p>")
        elif line.startswith("## "):
            html_parts.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("- "):
            html_parts.append(f"<li>{line[2:]}</li>")
        elif line.strip() == "":
            html_parts.append("<br>")
        else:
            html_parts.append(f"<p>{line}</p>")
    return jsonify({"html": "".join(html_parts)})


@cms_bp.route("/api/check-update", methods=["GET"])
@login_required
def check_update() -> flask.Response:
    """
    Check if a newer version or new commits are available on GitHub.
    Compares version + commit SHA to detect updates even without version bumps.
    Results are cached in-memory for 1 hour.
    """
    from version import get_version

    current_ver = get_version()

    repo = Setting.get("update_check_repo")
    if not repo:
        return jsonify(
            {
                "update_available": False,
                "current_version": current_ver,
                "latest_version": current_ver,
                "check_enabled": False,
                "message": "Update checking is disabled. Set update_check_repo in Settings.",
            }
        )

    # In-memory cache on the app
    cache_key = "_update_check_cache"
    cache = current_app.config.get(cache_key, {})
    now = time.time()

    if cache.get("cached_at") and (now - cache["cached_at"]) < 3600:
        return jsonify(cache["data"])

    try:
        import httpx

        # Fetch remote VERSION file
        ver_url = f"https://raw.githubusercontent.com/{repo}/master/VERSION"
        r = httpx.get(ver_url, timeout=10)
        r.raise_for_status()
        latest_ver = r.text.strip()

        # Fetch remote HEAD commit SHA via GitHub API
        local_sha = Setting.get("last_update_commit", "")
        remote_sha = local_sha
        try:
            api_url = f"https://api.github.com/repos/{repo}/commits/master"
            api_r = httpx.get(
                api_url, timeout=10, headers={"Accept": "application/vnd.github.v3.sha"}
            )
            if api_r.status_code == 200:
                remote_sha = api_r.text.strip()
        except Exception as e:
            logger.debug(
                f"Failed to fetch remote SHA from GitHub ({type(e).__name__}): {e}"
            )

        # If no stored local SHA, try to get it from the git repo and store it now
        if not local_sha and remote_sha:
            import subprocess as sp
            import shutil

            try:
                git_path = shutil.which("git") or "/usr/bin/git"
                r = sp.run(
                    [git_path, "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=current_app.root_path,
                    timeout=10,
                )
                if r.returncode == 0:
                    local_sha = r.stdout.strip()
                    Setting.set(
                        "last_update_commit",
                        local_sha,
                        "Last pulled commit SHA (auto-updated)",
                        "general",
                    )
                    logger.info(f"Stored initial commit SHA: {local_sha[:12]}")
            except Exception as e:
                logger.debug(f"Failed to run git rev-parse ({type(e).__name__}): {e}")

        current_parts = [int(x) for x in current_ver.split(".")]
        latest_parts = [int(x) for x in latest_ver.split(".")]
        version_update = latest_parts > current_parts
        commits_update = bool(
            remote_sha and local_sha and remote_sha != local_sha and not version_update
        )
        update_available = version_update or commits_update

        # Fetch changelog if update is available
        changelog = None
        if update_available:
            try:
                cl_url = f"https://raw.githubusercontent.com/{repo}/master/CHANGELOG.md"
                cl_r = httpx.get(cl_url, timeout=10)
                if cl_r.status_code == 200:
                    cl_text = cl_r.text
                    m = re.search(
                        r"##\s*\[([^\]]+)\].*?(?=\n##\s|\Z)", cl_text, re.DOTALL
                    )
                    if m:
                        changelog = m.group(0).strip()
            except Exception as e:
                logger.debug(f"Failed to fetch CHANGELOG ({type(e).__name__}): {e}")

        data = {
            "update_available": update_available,
            "version_update": version_update,
            "commits_update": commits_update,
            "current_version": current_ver,
            "latest_version": latest_ver if version_update else current_ver,
            "check_enabled": True,
            "repo": repo,
            "remote_sha": remote_sha,
            "local_sha": local_sha,
            "changelog": changelog,
        }

        current_app.config[cache_key] = {"data": data, "cached_at": now}
        return jsonify(data)

    except Exception:
        logger.exception("Update check failed")
        return jsonify(
            {
                "update_available": False,
                "current_version": current_ver,
                "latest_version": None,
                "check_enabled": True,
                "error": "Update check failed",
            }
        )


@cms_bp.route("/admin/do-update", methods=["POST"])
@csrf.exempt
@login_required
@admin_required
def do_update() -> flask.Response:
    """
    Run update: backup, git pull, pip upgrade, restart services.
    Admin only. Runs synchronously and streams status via JSON responses.
    """
    try:
        import subprocess
        import sys
        from datetime import datetime
        from version import get_version

        current_ver = get_version()
        results = []

        def step(msg, cmd_list, cwd=None, env=None):
            results.append({"step": msg, "status": "running"})
            try:
                r = subprocess.run(
                    cmd_list,
                    capture_output=True,
                    text=True,
                    cwd=cwd or current_app.root_path,
                    timeout=120,
                    env=env,
                )
                if r.returncode == 0:
                    results[-1] = {
                        "step": msg,
                        "status": "ok",
                        "output": r.stdout.strip(),
                    }
                elif r.returncode < 0 and "restart" in msg.lower():
                    results[-1] = {
                        "step": msg,
                        "status": "ok",
                        "output": "Service restarted (process killed by signal, expected)",
                    }
                else:
                    output = (
                        r.stderr.strip()
                        or r.stdout.strip()
                        or f"Command failed (exit code {r.returncode})"
                    )
                    results[-1] = {"step": msg, "status": "error", "output": output}
                    logger.error(f"Update step failed: {msg}\n{output}")
            except Exception:
                logger.exception("Update step exception: %s", msg)
                results[-1] = {"step": msg, "status": "error", "output": "Step failed"}

        import shutil

        project_root = current_app.root_path

        # Step 1: Database backup (SQLite only)
        db_path = current_app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///cms.db")
        if db_path.startswith("sqlite"):
            db_file = db_path.replace("sqlite:///", "")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            step("Backup database", ["cp", db_file, f"{db_file}.backup.{timestamp}"])

        # Step 2: Git pull
        git_path = shutil.which("git") or "/usr/bin/git"
        step(
            "Pull latest code", [git_path, "pull", "origin", "master"], cwd=project_root
        )

        # Step 3: Install dependencies
        step(
            "Update Python packages",
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                "requirements.txt",
                "--upgrade",
            ],
            cwd=project_root,
        )

        # Step 4: Apply Alembic migrations
        import os as _os

        alembic_env = {**_os.environ}
        alembic_env["DATABASE_URL"] = db_path
        if "CMS_ENCRYPTION_KEY" not in alembic_env:
            alembic_env["CMS_ENCRYPTION_KEY"] = current_app.config.get(
                "CMS_ENCRYPTION_KEY", ""
            )
        step(
            "Apply database migrations",
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=project_root,
            env=alembic_env,
        )

        # Step 5: Restart (async — response must be sent before process kill)
        restart_proc = subprocess.Popen(
            ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "osint-dashboard"],
            cwd=project_root,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        results.append(
            {
                "step": "Restart services",
                "status": "ok",
                "output": f"Restart initiated (PID {restart_proc.pid})",
            }
        )
        logger.info(f"Restart initiated (PID {restart_proc.pid})")

        success = all(r["status"] == "ok" for r in results)

        # Store local HEAD SHA after pull
        pull_ok = any(
            r["step"] == "Pull latest code" and r["status"] == "ok" for r in results
        )
        if pull_ok:
            try:
                import subprocess as sp

                git_path = shutil.which("git") or "/usr/bin/git"
                sha_result = sp.run(
                    [git_path, "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=project_root,
                    timeout=15,
                )
                if sha_result.returncode == 0:
                    head_sha = sha_result.stdout.strip()
                    Setting.set(
                        "last_update_commit",
                        head_sha,
                        "Last pulled commit SHA (auto-updated)",
                        "general",
                    )
                    logger.info(f"Stored last update commit: {head_sha[:12]}")
            except Exception as e:
                logger.warning(f"Failed to store commit SHA ({type(e).__name__}): {e}")

        return jsonify(
            {
                "success": success,
                "current_version": current_ver,
                "results": results,
                "message": "Update completed successfully"
                if success
                else "Update had errors, check results",
            }
        ), 200 if success else 500
    except Exception:
        logger.exception("do_update crashed")
        return jsonify(
            {
                "success": False,
                "current_version": "unknown",
                "results": [
                    {
                        "step": "Update crashed",
                        "status": "error",
                        "output": "Update crashed",
                    }
                ],
                "message": "Update crashed with an unexpected error",
            }
        ), 500
