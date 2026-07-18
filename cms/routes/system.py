import json as _json
import logging
import os
import re
import time

import flask
from flask import jsonify, current_app, render_template
from flask_login import login_required, current_user

from . import cms_bp
from ..models import Setting
from ..auth import admin_required
from cms.services.http_utils import jittered_get

from .response import api_success, api_error

logger = logging.getLogger(__name__)


_SESSION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "flask_session"
)


def _get_session_backend() -> str:
    """Detect session backend: 'redis' or 'filesystem'."""
    try:
        from flask import current_app

        return current_app.config.get("SESSION_TYPE", "cachelib")
    except Exception:
        return "cachelib"


def _get_redis_client():
    """Get Redis client from app config if available."""
    try:
        from flask import current_app

        return current_app.config.get("SESSION_REDIS")
    except Exception:
        return None


def _all_session_ids() -> list[str]:
    """Return all active session IDs from the current backend."""
    backend = _get_session_backend()
    if backend == "redis":
        client = _get_redis_client()
        if client:
            try:
                keys = client.keys("session:*")
                return [k.split(":", 1)[1] for k in keys]
            except Exception:
                logger.exception("Failed to list Redis sessions")
                return []
    # Filesystem fallback
    import os as _os

    if not _os.path.exists(_SESSION_DIR):
        return []
    return [
        f.replace("session_", "")
        for f in sorted(_os.listdir(_SESSION_DIR), reverse=True)
        if f.startswith("session_")
    ]


def _delete_session(session_id: str) -> bool:
    """Delete a session by ID from the current backend."""
    backend = _get_session_backend()
    if backend == "redis":
        client = _get_redis_client()
        if client:
            try:
                deleted = client.delete(f"session:{session_id}")
                return deleted > 0
            except Exception:
                logger.exception("Failed to delete Redis session")
                return False
    # Filesystem fallback
    import os as _os

    session_file = _os.path.normpath(
        _os.path.join(_SESSION_DIR, f"session_{session_id}")
    )
    if not session_file.startswith(_os.path.normpath(_SESSION_DIR)):
        return False
    if _os.path.exists(session_file):
        _os.remove(session_file)
        return True
    return False


def _read_session_data(session_id: str) -> dict:
    """Read and deserialize a session from the current backend."""
    import json

    backend = _get_session_backend()
    if backend == "redis":
        client = _get_redis_client()
        if client:
            try:
                raw = client.get(f"session:{session_id}")
                if raw:
                    data = json.loads(raw)
                    return {
                        "user_id": data.get("_user_id"),
                        "created_at": data.get("_created_at"),
                        "ip": data.get("_ip", data.get("ip", "")),
                    }
            except Exception:
                logger.exception("Failed to read Redis session")
            return {}
    # Filesystem fallback
    import os as _os

    session_file = _os.path.normpath(
        _os.path.join(_SESSION_DIR, f"session_{session_id}")
    )
    if not session_file.startswith(_os.path.normpath(_SESSION_DIR)):
        return {}
    try:
        if _os.path.exists(session_file):
            with open(session_file) as f:
                raw = f.read()
            data = json.loads(raw)
            return {
                "user_id": data.get("_user_id"),
                "created_at": data.get("_created_at"),
                "ip": data.get("_ip", data.get("ip", "")),
            }
    except Exception:
        logger.exception("Failed to read session data")
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
    for sid in _all_session_ids():
        data = _read_session_data(sid)
        sessions.append(
            {
                "session_id": sid,
                "user_id": data.get("user_id"),
                "ip": data.get("ip"),
                "is_current": sid == flask.session.sid
                if hasattr(flask.session, "sid")
                else False,
            }
        )
    return jsonify({"sessions": sessions, "count": len(sessions)})


@cms_bp.route("/admin/sessions/<session_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_session(session_id: str) -> flask.Response:
    """Delete a specific session."""
    if _delete_session(session_id):
        return api_success({}, "Session deleted")
    return api_error("Session not found", 404)


@cms_bp.route("/admin/sessions/delete-all", methods=["POST"])
@login_required
@admin_required
def delete_all_sessions() -> flask.Response:
    """Delete all sessions except the current one."""
    count = 0
    current_sid = getattr(flask.session, "sid", None)
    for sid in _all_session_ids():
        if sid == current_sid:
            continue
        if _delete_session(sid):
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
        # Fetch remote VERSION file
        ver_url = f"https://raw.githubusercontent.com/{repo}/master/VERSION"
        r = jittered_get(ver_url, timeout=10)
        r.raise_for_status()
        latest_ver = r.text.strip()

        # Fetch remote HEAD commit SHA via GitHub API
        local_sha = Setting.get("last_update_commit", "")
        remote_sha = local_sha
        try:
            api_url = f"https://api.github.com/repos/{repo}/commits/master"
            api_r = jittered_get(
                api_url,
                timeout=10,
                headers={"Accept": "application/vnd.github.v3.sha"},
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
                logger.debug(
                    f"git rev-parse failed ({type(e).__name__}): {e} — using remote SHA as baseline"
                )
                local_sha = remote_sha
                Setting.set(
                    "last_update_commit",
                    local_sha,
                    "Last pulled commit SHA (auto-set from remote)",
                    "general",
                )

        # Strip leading 'v' or 'V' from version strings for numeric comparison
        def _parse_ver(v: str):
            clean = v.lstrip("vV").split("-")[0]  # "v2.0.0-alpha" → "2.0.0"
            return [int(x) for x in clean.split(".")]

        current_parts = _parse_ver(current_ver)
        latest_parts = _parse_ver(latest_ver)
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
                cl_r = jittered_get(cl_url, timeout=10)
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


# -------------------------------------------------------------------
# Async update: in-memory / file-based task tracking
# -------------------------------------------------------------------
_UPDATE_TASKS_DIR = "/tmp/iveras_update_tasks"


def _ensure_task_dir():
    if not os.path.isdir(_UPDATE_TASKS_DIR):
        os.makedirs(_UPDATE_TASKS_DIR, exist_ok=True)


def _task_file_path(task_id: str) -> str:
    return os.path.join(_UPDATE_TASKS_DIR, f"{task_id}.json")


def _write_task(task_id: str, data: dict):
    _ensure_task_dir()
    tmp = _task_file_path(task_id) + ".tmp"
    try:
        with open(tmp, "w") as f:
            _json.dump(data, f)
        os.rename(tmp, _task_file_path(task_id))
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _read_task(task_id: str) -> dict | None:
    try:
        with open(_task_file_path(task_id)) as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return None


def _run_update_background(task_id: str, app):
    """Background thread: runs update steps, writes progress to task file."""
    import subprocess
    import sys
    from datetime import datetime
    from version import get_version

    with app.app_context():
        try:

            def _save():
                _write_task(task_id, task)

            def _step(msg, cmd_list, cwd=None, env=None):
                task["results"].append({"step": msg, "status": "running"})
                _save()
                try:
                    r = subprocess.run(
                        cmd_list,
                        capture_output=True,
                        text=True,
                        cwd=cwd or app.root_path,
                        timeout=300,
                        env=env,
                    )
                    if _abort_check():
                        return
                    if r.returncode == 0:
                        task["results"][-1] = {
                            "step": msg,
                            "status": "ok",
                            "output": r.stdout.strip(),
                        }
                    elif r.returncode < 0 and "restart" in msg.lower():
                        task["results"][-1] = {
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
                        task["results"][-1] = {
                            "step": msg,
                            "status": "error",
                            "output": output,
                        }
                        logger.error("Update step failed: %s\n%s", msg, output)
                except Exception:
                    logger.exception("Update step exception: %s", msg)
                    task["results"][-1] = {
                        "step": msg,
                        "status": "error",
                        "output": "Step failed",
                    }
                _save()

            def _abort_check():
                t = _read_task(task_id)
                return t is not None and t.get("aborted", False)

            task = _read_task(task_id)

            def _full_env():
                env = {**os.environ}
                env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + env.get(
                    "PATH", ""
                )
                return env

            if task is None:
                logger.error("Task %s not found at start of background thread", task_id)
                return
            task["status"] = "running"
            _save()

            project_root = app.root_path
            current_ver = get_version()
            venv_python = os.path.join(
                os.path.dirname(app.root_path), "venv", "bin", "python3"
            )
            python_bin = venv_python if os.path.isfile(venv_python) else sys.executable
            db_path = app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///cms.db")

            # Step 1: Full backup
            if _abort_check():
                return
            backup_script = os.path.join(project_root, "scripts", "backup.sh")
            if os.path.isfile(backup_script):
                os.chmod(backup_script, 0o755)
                _step(
                    "Full backup",
                    [backup_script, os.path.join(project_root, "backups")],
                    cwd=project_root,
                    env=_full_env(),
                )
            else:
                if db_path.startswith("sqlite"):
                    db_file = db_path.replace("sqlite:///", "")
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    _step(
                        "Backup database",
                        ["cp", db_file, f"{db_file}.backup.{timestamp}"],
                    )
            if _abort_check():
                return

            # Store backup path + pre-pull SHA for rollback
            try:
                latest_backup = None
                backup_dir = os.path.join(project_root, "backups")
                if os.path.isdir(backup_dir):
                    backups_ = sorted(
                        os.path.join(backup_dir, f)
                        for f in os.listdir(backup_dir)
                        if f.startswith("iveras_backup_") and f.endswith(".tar.gz.gpg")
                    )
                    if backups_:
                        latest_backup = backups_[-1]
                elif db_path.startswith("sqlite"):
                    db_file = db_path.replace("sqlite:///", "")
                    bc = sorted(
                        f
                        for f in os.listdir(os.path.dirname(db_file) or ".")
                        if f.startswith(os.path.basename(db_file) + ".backup.")
                    )
                    if bc:
                        latest_backup = os.path.join(
                            os.path.dirname(db_file) or ".", bc[-1]
                        )
                if latest_backup:
                    _cfg_set("last_backup_path", latest_backup)
                pre_sha_r = subprocess.run(
                    ["/usr/bin/git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=project_root,
                    timeout=15,
                )
                if pre_sha_r.returncode == 0:
                    _cfg_set("pre_update_commit", pre_sha_r.stdout.strip())
            except Exception as e:
                logger.warning("Rollback metadata error: %s", e)

            if _abort_check():
                return

            # Step 2: Git pull
            _step(
                "Pull latest code",
                ["/usr/bin/sudo", "/usr/bin/git", "pull", "origin", "master"],
                cwd=project_root,
            )
            if _abort_check():
                return

            # Step 3: pip install
            _step(
                "Update Python packages",
                [
                    python_bin,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    "requirements.txt",
                    "--upgrade",
                ],
                cwd=project_root,
            )
            if _abort_check():
                return

            # Step 4: Alembic
            alembic_env = {**os.environ}
            alembic_env["DATABASE_URL"] = db_path
            if "CMS_ENCRYPTION_KEY" not in alembic_env:
                alembic_env["CMS_ENCRYPTION_KEY"] = app.config.get(
                    "CMS_ENCRYPTION_KEY", ""
                )
            _step(
                "Apply database migrations",
                [python_bin, "-m", "alembic", "upgrade", "head"],
                cwd=project_root,
                env=alembic_env,
            )
            if _abort_check():
                return

            # Store post-pull commit SHA
            try:
                sha_r = subprocess.run(
                    ["/usr/bin/git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=project_root,
                    timeout=15,
                )
                if sha_r.returncode == 0:
                    _cfg_set("last_update_commit", sha_r.stdout.strip())
            except Exception:
                pass

            # Step 5: Restart — set status to restarting so frontend knows
            task["status"] = "restarting"
            _save()
            subprocess.Popen(
                [
                    "/usr/bin/sudo",
                    "/bin/sh",
                    "-c",
                    "sleep 3 && /usr/bin/systemctl restart osint-dashboard",
                ],
                cwd=project_root,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            task["results"].append(
                {
                    "step": "Restart services",
                    "status": "ok",
                    "output": "Restart initiated (server will reload shortly)",
                }
            )
            task["success"] = all(r["status"] == "ok" for r in task["results"])
            task["message"] = (
                "Update completed successfully"
                if task["success"]
                else "Update had errors"
            )
            task["status"] = "done"
            _save()

            # Send notification email (best effort)
            _send_update_email(app, task, current_ver)

        except Exception:
            logger.exception("Background update crashed")
            task = _read_task(task_id) or {"task_id": task_id, "results": []}
            task["status"] = "error"
            task["success"] = False
            task["message"] = "Update crashed with an unexpected error"
            task["results"].append(
                {
                    "step": "Background worker",
                    "status": "error",
                    "output": "Update crashed",
                }
            )
            _write_task(task_id, task)


def _cfg_set(key: str, value: str):
    try:
        Setting.set(key, value, "", "general")
    except Exception:
        pass


def _send_update_email(app, task: dict, current_ver: str):
    """Best-effort notification email after update completes."""
    try:
        from ..email_utils import send_email, is_smtp_configured
        from ..models import User
        from datetime import datetime

        with app.app_context():
            superadmins = User.query.filter_by(is_super_admin=True).all()
            if not superadmins or not is_smtp_configured():
                return
            results = task.get("results", [])
            success = task.get("success", False)
            project_root = app.root_path
            backup_dir = os.path.join(project_root, "backups")
            latest = None
            if os.path.isdir(backup_dir):
                files = sorted(
                    os.path.join(backup_dir, f)
                    for f in os.listdir(backup_dir)
                    if f.startswith("iveras_backup_") and f.endswith(".tar.gz.gpg")
                )
                if files:
                    latest = files[-1]
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status_icon = "✅" if success else "❌"
            status_text = "geslaagd" if success else "mislukt"
            subject = f"{status_icon} Iveras update {status_text} — {now_str}"
            steps_html = ""
            for r in results:
                s = r["status"]
                icon = "✅" if s == "ok" else "❌" if s == "error" else "⏳"
                out = (r.get("output") or "")[:200]
                steps_html += (
                    f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee;'>{icon}</td>"
                    f"<td style='padding:6px 12px;border-bottom:1px solid #eee;'>{r['step']}</td>"
                    f"<td style='padding:6px 12px;border-bottom:1px solid #eee;font-family:monospace;font-size:0.85rem;'>{out}</td></tr>"
                )
            backup_info = ""
            if latest:
                backup_info = f"""
                <tr><td style='padding:6px 12px;font-weight:600;'>Backup bestand</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{latest}</td></tr>
                <tr><td style='padding:6px 12px;font-weight:600;'>Backup key</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{os.path.join(backup_dir, "backup-key.gpg")}</td></tr>
                """
            body_html = f"""<html><body style="font-family:sans-serif;padding:2rem;max-width:700px;">
<h2>{status_icon} Iveras update {status_text}</h2>
<p>Er is een update uitgevoerd via de web interface.</p>
<table style="width:100%;border-collapse:collapse;margin:1rem 0;">
<tr><td style="padding:6px 12px;font-weight:600;">Datum/tijd</td><td style="padding:6px 12px;">{now_str}</td></tr>
<tr><td style="padding:6px 12px;font-weight:600;">Status</td><td style="padding:6px 12px;">{status_text}</td></tr>
<tr><td style="padding:6px 12px;font-weight:600;">Versie</td><td style="padding:6px 12px;">{current_ver}</td></tr>
{backup_info}
</table>
<h3>Stappen</h3>
<table style="width:100%;border-collapse:collapse;">{steps_html}</table>
<h3>Herstel bij problemen</h3>
<pre style="background:#f5f5f5;padding:1rem;border-radius:6px;font-size:0.85rem;">./scripts/restore.sh --list && ./scripts/restore.sh</pre>
</body></html>"""
            body_text = (
                f"Iveras update {status_text} — {now_str}\nVersie: {current_ver}\n"
            )
            for r in results:
                body_text += (
                    f"  {'OK' if r['status'] == 'ok' else 'FAIL'} {r['step']}\n"
                )
            for admin in superadmins:
                try:
                    send_email(admin.email, subject, body_html, body_text)
                except Exception:
                    pass
    except Exception:
        pass


@cms_bp.route("/admin/do-update", methods=["POST"])
@login_required
@admin_required
def do_update() -> flask.Response:
    """
    Start update in background thread. Returns task_id for polling.
    """
    import uuid

    task_id = uuid.uuid4().hex[:16]
    task = {
        "task_id": task_id,
        "status": "starting",
        "success": False,
        "message": "",
        "results": [],
        "aborted": False,
    }
    _write_task(task_id, task)

    import threading

    t = threading.Thread(
        target=_run_update_background,
        args=(task_id, current_app._get_current_object()),
        daemon=True,
    )
    t.start()

    return jsonify({"task_id": task_id}), 202


@cms_bp.route("/admin/update-status/<task_id>")
@login_required
@admin_required
def update_status(task_id: str) -> flask.Response:
    """Polling endpoint — returns current task state."""
    task = _read_task(task_id)
    if task is None:
        return jsonify({"status": "not_found", "message": "Task not found"}), 404
    return jsonify(
        {
            "task_id": task["task_id"],
            "status": task["status"],
            "success": task.get("success", False),
            "message": task.get("message", ""),
            "results": task.get("results", []),
            "aborted": task.get("aborted", False),
        }
    )


@cms_bp.route("/admin/abort-update/<task_id>", methods=["POST"])
@login_required
@admin_required
def abort_update(task_id: str) -> flask.Response:
    """Set abort flag — background thread will stop after current step."""
    task = _read_task(task_id)
    if task is None:
        return jsonify({"status": "not_found"}), 404
    task["aborted"] = True
    if task["status"] in ("running", "starting"):
        task["status"] = "aborting"
    _write_task(task_id, task)
    return jsonify({"status": "aborting"})


@cms_bp.route("/admin/rollback-update")
@login_required
@admin_required
def rollback_update() -> flask.Response:
    """
    Rollback the last update: restore backup + git reset --hard to pre-pull commit.
    Admin only.
    """
    try:
        import os as _os
        import subprocess

        project_root = current_app.root_path
        results = []

        def step(msg, cmd_list, cwd=None, env=None):
            results.append({"step": msg, "status": "running"})
            try:
                r = subprocess.run(
                    cmd_list,
                    capture_output=True,
                    text=True,
                    cwd=cwd or project_root,
                    timeout=120,
                    env=env,
                )
                if r.returncode == 0:
                    results[-1] = {
                        "step": msg,
                        "status": "ok",
                        "output": r.stdout.strip(),
                    }
                else:
                    output = (
                        r.stderr.strip()
                        or r.stdout.strip()
                        or f"Exit code {r.returncode}"
                    )
                    results[-1] = {"step": msg, "status": "error", "output": output}
                    logger.error("Rollback step failed: %s\n%s", msg, output)
            except Exception:
                logger.exception("Rollback step exception: %s", msg)
                results[-1] = {"step": msg, "status": "error", "output": "Step failed"}

        # Step 1: Find backup
        backup_path = Setting.get("last_backup_path")
        if not backup_path or not _os.path.isfile(backup_path):
            # Fallback: probeer de meest recente backup te vinden
            backup_dir = _os.path.join(project_root, "backups")
            if _os.path.isdir(backup_dir):
                files = sorted(
                    _os.path.join(backup_dir, f)
                    for f in _os.listdir(backup_dir)
                    if f.startswith("iveras_backup_") and f.endswith(".tar.gz.gpg")
                )
                if files:
                    backup_path = files[-1]
            if not backup_path or not _os.path.isfile(backup_path):
                # Probeer SQLite fallback
                db_path = current_app.config.get(
                    "SQLALCHEMY_DATABASE_URI", "sqlite:///cms.db"
                )
                if db_path.startswith("sqlite"):
                    db_file = db_path.replace("sqlite:///", "")
                    backup_candidates = sorted(
                        f
                        for f in _os.listdir(_os.path.dirname(db_file) or ".")
                        if f.startswith(_os.path.basename(db_file) + ".backup.")
                    )
                    if backup_candidates:
                        backup_path = _os.path.join(
                            _os.path.dirname(db_file) or ".", backup_candidates[-1]
                        )

        if not backup_path or not _os.path.isfile(backup_path):
            return jsonify(
                {
                    "success": False,
                    "results": [
                        {
                            "step": "Backup zoeken",
                            "status": "error",
                            "output": "Geen backup gevonden. Rollback niet mogelijk.",
                        }
                    ],
                    "message": "Geen backup gevonden",
                }
            ), 500

        # Step 2: Restore from backup
        restore_script = _os.path.join(project_root, "scripts", "restore.sh")
        if _os.path.isfile(restore_script):
            _os.chmod(restore_script, 0o755)
            step(
                "Database herstellen",
                [
                    restore_script,
                    "--backup",
                    backup_path,
                    "--key",
                    _os.path.join(_os.path.dirname(backup_path), "backup-key.gpg"),
                ],
                cwd=project_root,
            )
        elif backup_path.endswith(".db.backup."):
            # SQLite fallback restore
            db_path = current_app.config.get(
                "SQLALCHEMY_DATABASE_URI", "sqlite:///cms.db"
            )
            db_file = db_path.replace("sqlite:///", "")
            step("Database herstellen", ["cp", backup_path, db_file])

        # Step 3: Git reset to pre-pull commit
        pre_sha = Setting.get("pre_update_commit")
        if pre_sha:
            step(
                "Git reset naar vorige commit",
                ["/usr/bin/sudo", "/usr/bin/git", "reset", "--hard", pre_sha],
                cwd=project_root,
            )
        else:
            # Fallback: gebruik ORIG_HEAD
            step(
                "Git reset naar ORIG_HEAD",
                ["/usr/bin/sudo", "/usr/bin/git", "reset", "--hard", "ORIG_HEAD"],
                cwd=project_root,
            )

        # Step 4: Restart services (delayed)
        restart_proc = subprocess.Popen(
            [
                "/usr/bin/sudo",
                "/bin/sh",
                "-c",
                "sleep 3 && /usr/bin/systemctl restart osint-dashboard",
            ],
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
        logger.info("Rollback restart scheduled in 3s (PID %s)", restart_proc.pid)

        success = all(r["status"] == "ok" for r in results)

        # Clean up rollback settings
        try:
            from ..models import db as _db

            s = Setting.query.filter_by(key="pre_update_commit").first()
            if s:
                _db.session.delete(s)
                _db.session.commit()
            s = Setting.query.filter_by(key="last_backup_path").first()
            if s:
                _db.session.delete(s)
                _db.session.commit()
        except Exception:
            try:
                _db.session.rollback()
            except Exception:
                pass

        return jsonify(
            {
                "success": success,
                "results": results,
                "message": "Rollback completed successfully"
                if success
                else "Rollback had errors",
            }
        ), 200 if success else 500

    except Exception:
        logger.exception("rollback_update crashed")
        return jsonify(
            {
                "success": False,
                "results": [
                    {
                        "step": "Rollback crashed",
                        "status": "error",
                        "output": "Rollback crashed",
                    }
                ],
                "message": "Rollback crashed",
            }
        ), 500


@cms_bp.route("/admin/brave-status")
@login_required
@admin_required
def brave_status():
    """Check Brave API remaining quota by making a test request."""
    from cms.services.search_service import _get_brave_key

    api_key = _get_brave_key()
    if not api_key:
        return render_template("cms/brave_status.html", data={"configured": False})

    result = {"configured": True}
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
    }
    try:
        resp = jittered_get(
            "https://api.search.brave.com/res/v1/web/search",
            timeout=10.0,
            headers=headers,
            params={"q": "test", "count": 1},
        )
        remaining_header = resp.headers.get("X-RateLimit-Remaining", "")
        limit_header = resp.headers.get("X-RateLimit-Limit", "")
        reset_header = resp.headers.get("X-RateLimit-Reset", "")
        result["status_code"] = resp.status_code
        logger.info(
            "Brave rate limit headers — limit=%r remaining=%r reset=%r",
            limit_header,
            remaining_header,
            reset_header,
        )
        if resp.status_code == 402:
            try:
                body = resp.json()
                result["api_error"] = body.get("error", body.get("message", str(body)))
            except Exception:
                result["api_error"] = resp.text[:500] or "Unknown error"
        try:
            parts_remaining = remaining_header.split(",")
            parts_limit = limit_header.split(",")
            parts_reset = reset_header.split(",")

            def _pick_monthly(parts):
                """Pick the monthly value from rate-limit header parts.
                If 2+ parts, the second is monthly (first is per-minute).
                If 1 part, use it directly as monthly limit (free tier)."""
                cleaned = [p.strip() for p in parts if p.strip()]
                if len(cleaned) >= 2:
                    return int(cleaned[1])
                if len(cleaned) == 1:
                    return int(cleaned[0])
                return None

            limit_val = _pick_monthly(parts_limit)
            if limit_val is not None:
                result["monthly_limit"] = limit_val

            remaining_val = _pick_monthly(parts_remaining)
            if remaining_val is not None:
                result["monthly_remaining"] = remaining_val

            reset_val = _pick_monthly(parts_reset)
            if reset_val is not None:
                result["monthly_reset_seconds"] = reset_val
                result["monthly_reset_days"] = round(
                    result["monthly_reset_seconds"] / 86400, 1
                )
        except (ValueError, IndexError):
            pass

        if result.get("monthly_remaining") is not None and result.get("monthly_limit"):
            pct = result["monthly_remaining"] / result["monthly_limit"] * 100
            result["pct_remaining"] = round(pct, 1)
            used = result["monthly_limit"] - result["monthly_remaining"]
            result["monthly_used"] = used
            cost_per_1000 = 5.0
            result["estimated_cost"] = round((used / 1000) * cost_per_1000, 2)
            result["plan"] = (
                f"$5/1000 requests — ${result['estimated_cost']:.2f} this month"
            )
            if pct < 20:
                result["warning"] = (
                    f"Brave quota bijna op: {result['monthly_remaining']}/{result['monthly_limit']} ({pct:.0f}%)"
                )
                logger.warning(
                    "Brave quota low: %s/%s (%s%%) — est. cost $%.2f this month",
                    result["monthly_remaining"],
                    result["monthly_limit"],
                    pct,
                    result["estimated_cost"],
                )
        else:
            result["pct_remaining"] = 100
            result["monthly_limit"] = result.get("monthly_limit", 2000)
            result["monthly_remaining"] = result.get("monthly_remaining", 0)
            result["monthly_used"] = result.get("monthly_used", 0)
            result["estimated_cost"] = result.get("estimated_cost", 0)

        return render_template("cms/brave_status.html", data=result)
    except Exception as e:
        logger.exception("Brave status check failed")
        return render_template(
            "cms/brave_status.html", data={"configured": True, "error": str(e)}
        )


# ── Announcements ──────────────────────────────────────────────────────────


@cms_bp.route("/admin/announcements")
@login_required
@admin_required
def list_announcements():
    from cms.models import Announcement

    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template("cms/announcements/list.html", announcements=announcements)


@cms_bp.route("/admin/announcements/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_announcement():
    from cms.models import Announcement, db
    from datetime import datetime

    if flask.request.method == "POST":
        title = flask.request.form.get("title", "").strip()
        body = flask.request.form.get("body", "").strip()
        severity = flask.request.form.get("severity", "info")
        expires_at_str = flask.request.form.get("expires_at", "").strip()

        if not title or not body:
            flask.flash("Titel en bericht zijn verplicht.", "danger")
            return render_template("cms/announcements/form.html", announcement=None)

        expires_at = None
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
            except ValueError:
                flask.flash("Ongeldige datum/tijd.", "danger")
                return render_template("cms/announcements/form.html", announcement=None)

        announcement = Announcement(
            title=title,
            body=body,
            severity=severity,
            expires_at=expires_at,
            created_by_id=current_user.id,
        )
        db.session.add(announcement)
        db.session.commit()
        flask.flash("Aankondiging aangemaakt.", "success")
        return flask.redirect(flask.url_for("cms.list_announcements"))

    return render_template("cms/announcements/form.html", announcement=None)


@cms_bp.route("/admin/announcements/<announcement_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_announcement(announcement_id):
    from cms.models import Announcement, db
    from datetime import datetime

    announcement = Announcement.query.get_or_404(announcement_id)

    if flask.request.method == "POST":
        announcement.title = flask.request.form.get("title", "").strip()
        announcement.body = flask.request.form.get("body", "").strip()
        announcement.severity = flask.request.form.get("severity", "info")
        expires_at_str = flask.request.form.get("expires_at", "").strip()

        if not announcement.title or not announcement.body:
            flask.flash("Titel en bericht zijn verplicht.", "danger")
            return render_template(
                "cms/announcements/form.html", announcement=announcement
            )

        if expires_at_str:
            try:
                announcement.expires_at = datetime.fromisoformat(expires_at_str)
            except ValueError:
                flask.flash("Ongeldige datum/tijd.", "danger")
                return render_template(
                    "cms/announcements/form.html", announcement=announcement
                )
        else:
            announcement.expires_at = None

        db.session.commit()
        flask.flash("Aankondiging bijgewerkt.", "success")
        return flask.redirect(flask.url_for("cms.list_announcements"))

    return render_template("cms/announcements/form.html", announcement=announcement)


@cms_bp.route("/admin/announcements/<announcement_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_announcement(announcement_id):
    from cms.models import Announcement, db

    announcement = Announcement.query.get_or_404(announcement_id)
    announcement.is_active = not announcement.is_active
    db.session.commit()
    flask.flash(
        f"Aankondiging {'geactiveerd' if announcement.is_active else 'gedeactiveerd'}.",
        "success",
    )
    return flask.redirect(flask.url_for("cms.list_announcements"))


@cms_bp.route("/admin/announcements/<announcement_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_announcement(announcement_id):
    from cms.models import Announcement, AnnouncementAck, db

    announcement = Announcement.query.get_or_404(announcement_id)
    AnnouncementAck.query.filter_by(announcement_id=announcement.id).delete()
    db.session.delete(announcement)
    db.session.commit()
    flask.flash("Aankondiging verwijderd.", "success")
    return flask.redirect(flask.url_for("cms.list_announcements"))


@cms_bp.route("/api/announcements/<announcement_id>/ack", methods=["POST"])
@login_required
def ack_announcement(announcement_id):
    from cms.models import Announcement, AnnouncementAck, db

    announcement = Announcement.query.get_or_404(announcement_id)
    existing = AnnouncementAck.query.filter_by(
        announcement_id=announcement.id, user_id=current_user.id
    ).first()
    if not existing:
        ack = AnnouncementAck(
            announcement_id=announcement.id,
            user_id=current_user.id,
        )
        db.session.add(ack)
        db.session.commit()
    return api_success({"status": "acknowledged"})
