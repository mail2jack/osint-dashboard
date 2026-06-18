import logging
import os
import re
import time

import flask
from flask import jsonify, current_app, render_template
from flask_login import login_required

from . import cms_bp
from ..models import Setting
from ..auth import admin_required
from curl_cffi import requests as curl_requests
from cms.services.http_utils import jitter_sleep

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

    session_file = _os.path.join(_SESSION_DIR, f"session_{session_id}")
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

    session_file = _os.path.join(_SESSION_DIR, f"session_{session_id}")
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
        jitter_sleep(domain_hint=ver_url)
        r = curl_requests.get(ver_url, timeout=10, impersonate="chrome124")
        r.raise_for_status()
        latest_ver = r.text.strip()

        # Fetch remote HEAD commit SHA via GitHub API
        local_sha = Setting.get("last_update_commit", "")
        remote_sha = local_sha
        try:
            api_url = f"https://api.github.com/repos/{repo}/commits/master"
            jitter_sleep(domain_hint=api_url)
            api_r = curl_requests.get(
                api_url,
                timeout=10,
                impersonate="chrome124",
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
                jitter_sleep(domain_hint=cl_url)
                cl_r = curl_requests.get(cl_url, timeout=10, impersonate="chrome124")
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

        project_root = current_app.root_path

        # Step 1: Full backup via scripts/backup.sh
        import os as _os

        db_path = current_app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///cms.db")
        backup_script = _os.path.join(project_root, "scripts", "backup.sh")
        if _os.path.isfile(backup_script):
            _os.chmod(backup_script, 0o755)
            step(
                "Full backup",
                [backup_script, _os.path.join(project_root, "backups")],
                cwd=project_root,
            )
        else:
            # Fallback: SQLite-only cp
            if db_path.startswith("sqlite"):
                db_file = db_path.replace("sqlite:///", "")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                step(
                    "Backup database", ["cp", db_file, f"{db_file}.backup.{timestamp}"]
                )

        # Step 2: Git pull (via sudo — Flask runs as osint, .git/ may be root-owned)
        # sudoers allows /usr/bin/git, NOT /usr/local/bin/git (Homebrew)
        sudo_git = "/usr/bin/git"
        step(
            "Pull latest code",
            ["/usr/bin/sudo", sudo_git, "pull", "origin", "master"],
            cwd=project_root,
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

                sha_result = sp.run(
                    ["/usr/bin/git", "rev-parse", "HEAD"],
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

        # Send notification email to all superadmins
        try:
            from ..email_utils import send_email, is_smtp_configured
            from ..models import User

            superadmins = User.query.filter_by(is_super_admin=True).all()
            if superadmins and is_smtp_configured():
                backup_dir = _os.path.join(project_root, "backups")
                latest = None
                if _os.path.isdir(backup_dir):
                    files = sorted(
                        _os.path.join(backup_dir, f)
                        for f in _os.listdir(backup_dir)
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
                    steps_html += f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee;'>{icon}</td><td style='padding:6px 12px;border-bottom:1px solid #eee;'>{r['step']}</td><td style='padding:6px 12px;border-bottom:1px solid #eee;font-family:monospace;font-size:0.85rem;'>{out}</td></tr>"

                backup_info = ""
                if latest:
                    backup_info = f"""
                    <tr><td style='padding:6px 12px;font-weight:600;'>Backup bestand</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{latest}</td></tr>
                    <tr><td style='padding:6px 12px;font-weight:600;'>Backup key</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{_os.path.join(backup_dir, "backup-key.gpg")}</td></tr>
                    """
                else:
                    backup_info = "<tr><td style='padding:6px 12px;' colspan='2'>⚠️ Geen backup gevonden (backup script mogelijk niet uitgevoerd)</td></tr>"

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
<p style="background:#fff3cd;border:1px solid #ffc107;padding:1rem;border-radius:6px;">
SSH naar de server en gebruik:
</p>
<pre style="background:#f5f5f5;padding:1rem;border-radius:6px;font-size:0.85rem;">
# Bekijk beschikbare backups
./scripts/restore.sh --list

# Herstel de laatste backup (vraagt bevestiging)
./scripts/restore.sh

# Na herstel de service herstarten
sudo systemctl restart osint-dashboard
</pre>
<p style="color:#666;font-size:0.85rem;">Dit bericht is automatisch gegenereerd door Iveras CMS.</p>
</body></html>"""

                body_text = f"Iveras update {status_text} — {now_str}\n\n"
                body_text += f"Versie: {current_ver}\n"
                if latest:
                    body_text += f"Backup: {latest}\n"
                body_text += "\nStappen:\n"
                for r in results:
                    body_text += (
                        f"  {'OK' if r['status'] == 'ok' else 'FAIL'} {r['step']}\n"
                    )
                body_text += (
                    "\nHerstel: SSH naar server en draai ./scripts/restore.sh\n"
                )

                for admin in superadmins:
                    try:
                        send_email(admin.email, subject, body_html, body_text)
                    except Exception as e:
                        logger.warning(
                            f"Failed to email update notification to {admin.email}: {e}"
                        )
        except Exception as e:
            logger.warning(f"Failed to send update notification email: {e}")

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
