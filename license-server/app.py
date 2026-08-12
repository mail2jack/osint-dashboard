"""
Iveras License & Telemetry Server.

Central registry for all OSINT Dashboard installs:

    POST /api/register   — register a new install (idempotent), auto-issues a trial license
    POST /api/telemetry  — daily heartbeat + updated system info
    GET  /api/license    — signed Ed25519 license for the install (offline-verifiable)
    GET  /               — registry dashboard (HTTP Basic Auth)
    GET  /api/installs   — registry as JSON (HTTP Basic Auth)
    GET  /health         — health check

Storage: SQLite via the stdlib sqlite3 module. Dependencies: Flask + gunicorn.
Licenses: Ed25519-signed claims; issue/revoke via `cli.py`.

Runtime config (env vars):
    LICENSE_DB_PATH     sqlite file path (default ./data/license.db)
    ADMIN_USER          basic-auth user for the dashboard
    ADMIN_PASSWORD      basic-auth password for the dashboard
    TRIAL_DAYS          trial license length in days for new installs (default 30)
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, render_template, request, session

import licensing

app = Flask(__name__)
app.secret_key = os.environ.get("LICENSE_ADMIN_SECRET") or os.urandom(32).hex()

DB_PATH = os.environ.get(
    "LICENSE_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "license.db")
)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

MAX_TEXT = 500
MAX_JSON = 8192


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS installs (
                install_id      TEXT PRIMARY KEY,
                token_hash      TEXT NOT NULL,
                hostname        TEXT,
                public_ip       TEXT,
                local_ips       TEXT,
                os_name         TEXT,
                os_version      TEXT,
                kernel          TEXT,
                cpu_model       TEXT,
                cpu_count       INTEGER,
                ram_gb          REAL,
                disk_gb         REAL,
                app_version     TEXT,
                platform        TEXT,
                registered_at   TEXT,
                last_seen       TEXT,
                last_ip         TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                license_id   TEXT PRIMARY KEY,
                install_id   TEXT NOT NULL UNIQUE,
                plan         TEXT NOT NULL DEFAULT 'trial',
                payload      TEXT NOT NULL,
                signature    TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'active',
                created_at   TEXT,
                expires_at   TEXT
            )
            """
        )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean(value, max_len=MAX_TEXT):
    if value is None:
        return None
    value = str(value).strip()
    return value[:max_len] if value else None


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    return {
        "install_id": row["install_id"],
        "hostname": row["hostname"],
        "public_ip": row["public_ip"],
        "local_ips": json.loads(row["local_ips"]) if row["local_ips"] else [],
        "os_name": row["os_name"],
        "os_version": row["os_version"],
        "kernel": row["kernel"],
        "cpu_model": row["cpu_model"],
        "cpu_count": row["cpu_count"],
        "ram_gb": row["ram_gb"],
        "disk_gb": row["disk_gb"],
        "app_version": row["app_version"],
        "platform": row["platform"],
        "registered_at": row["registered_at"],
        "last_seen": row["last_seen"],
        "last_ip": row["last_ip"],
    }


def _license_row(conn, install_id):
    return conn.execute(
        "SELECT * FROM licenses WHERE install_id = ? ORDER BY created_at DESC LIMIT 1",
        (install_id,),
    ).fetchone()


def _license_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "license_id": row["license_id"],
        "plan": row["plan"],
        "payload": row["payload"],
        "signature": row["signature"],
        "status": row["status"],
        "expires_at": row["expires_at"],
    }


def _issue_trial_if_needed(conn, install_id) -> None:
    if _license_row(conn, install_id) is not None:
        return
    days = int(os.environ.get("TRIAL_DAYS", "30"))
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        private_key = licensing.load_private_key()
        claims, signature = licensing.build_license(
            install_id, plan="trial", expires_at=expires_at, private_key=private_key
        )
    except Exception:
        app.logger.warning(
            "Could not issue trial license for %s (private key missing?)",
            install_id,
            exc_info=True,
        )
        return
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True)
    conn.execute(
        "INSERT INTO licenses (license_id, install_id, plan, payload, signature, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            claims["license_id"],
            install_id,
            "trial",
            payload,
            signature,
            "active",
            claims["issued_at"],
            claims["expires_at"],
        ),
    )
    app.logger.info(
        "Auto-issued trial license %s for %s (expires %s)",
        claims["license_id"],
        install_id,
        claims["expires_at"],
    )


def _issue_license(conn, install_id, plan="full", expires_at=None) -> dict:
    """Issue a signed license for an install, replacing any previous one.

    Shared by the CLI (cli.py license:new) and the web dashboard
    (POST /license/issue).
    """
    private_key = licensing.load_private_key()
    if private_key is None:
        raise FileNotFoundError(
            f"No license private key at {licensing.PRIVATE_KEY_PATH} — "
            "run cli.py keys:generate"
        )
    claims, signature = licensing.build_license(
        install_id, plan=plan, expires_at=expires_at, private_key=private_key
    )
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True)
    conn.execute("DELETE FROM licenses WHERE install_id = ?", (install_id,))
    conn.execute(
        "INSERT INTO licenses (license_id, install_id, plan, payload, signature, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            claims["license_id"],
            install_id,
            plan,
            payload,
            signature,
            "active",
            claims["issued_at"],
            claims["expires_at"],
        ),
    )
    return _license_dict(_license_row(conn, install_id))


def _revoke_license(conn, install_id) -> int:
    """Mark the install's license revoked. Returns the number of rows changed."""
    cur = conn.execute(
        "UPDATE licenses SET status = 'revoked' "
        "WHERE install_id = ? AND status != 'revoked'",
        (install_id,),
    )
    return cur.rowcount


def _delete_install(conn, install_id) -> tuple[int, int]:
    """Remove an install and its licenses. Returns (installs_deleted, licenses_deleted)."""
    lic = conn.execute("DELETE FROM licenses WHERE install_id = ?", (install_id,))
    ins = conn.execute("DELETE FROM installs WHERE install_id = ?", (install_id,))
    return ins.rowcount, lic.rowcount


def _json_body():
    try:
        return request.get_json(silent=True) or {}
    except Exception:
        return {}


def _auth_install(body) -> tuple[str | None, str | None]:
    install_id = _clean(body.get("install_id"), 100) or _clean(
        request.headers.get("X-Install-ID"), 100
    )
    auth = request.headers.get("Authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = auth[len("Bearer ") :].strip()
    if not install_id or not token:
        return None, None
    return install_id, _token_hash(token)


def _basic_auth_ok() -> bool:
    if not ADMIN_PASSWORD:
        return False
    auth = request.authorization
    if not auth:
        return False
    return hmac.compare_digest(auth.username or "", ADMIN_USER) and hmac.compare_digest(
        auth.password or "", ADMIN_PASSWORD
    )


def _basic_auth_guard():
    if _basic_auth_ok():
        return None
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Iveras License Server"'},
    )


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_guard():
    expected = session.get("_csrf_token")
    if not expected:
        return Response("CSRF token missing", 403)
    provided = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not provided or not hmac.compare_digest(provided, expected):
        return Response("CSRF token invalid", 403)
    return None


def _apply_info(body) -> dict:
    info = body.get("info") or {}
    if not isinstance(info, dict):
        info = {}
    fields = {
        "hostname": _clean(info.get("hostname")),
        "os_name": _clean(info.get("os_name")),
        "os_version": _clean(info.get("os_version")),
        "kernel": _clean(info.get("kernel")),
        "cpu_model": _clean(info.get("cpu_model")),
        "cpu_count": int(info.get("cpu_count") or 0) or None,
        "ram_gb": float(info.get("ram_gb") or 0) or None,
        "disk_gb": float(info.get("disk_gb") or 0) or None,
        "app_version": _clean(info.get("app_version")),
        "platform": _clean(info.get("platform")) or "unknown",
    }
    local_ips = info.get("local_ips")
    if isinstance(local_ips, list):
        fields["local_ips"] = json.dumps([str(i)[:45] for i in local_ips][:8])
    elif isinstance(local_ips, str):
        fields["local_ips"] = json.dumps([local_ips[:45]])
    return {key: value for key, value in fields.items() if value is not None}


def _update_install(conn, install_id, token_hash, fields, is_new):
    now = _now()
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
    )
    data = dict(fields)
    data["last_seen"] = now
    data["last_ip"] = _clean(client_ip, 64)
    if is_new:
        data["registered_at"] = now
        data["token_hash"] = token_hash
        placeholders = ", ".join(data.keys())
        values = ", ".join(f":{key}" for key in data)
        conn.execute(
            f"INSERT INTO installs (install_id, {placeholders}) VALUES (:install_id, {values})",
            {"install_id": install_id, **data},
        )
    else:
        assignments = ", ".join(f"{key} = :{key}" for key in data)
        conn.execute(
            f"UPDATE installs SET {assignments} WHERE install_id = :install_id",
            {"install_id": install_id, **data},
        )
    return now


@app.route("/api/register", methods=["POST"])
def api_register():
    if request.content_length and request.content_length > MAX_JSON:
        return jsonify({"status": "error", "message": "payload too large"}), 413
    body = _json_body()
    install_id, token_hash = _auth_install(body)
    if not install_id or not token_hash:
        return jsonify(
            {"status": "error", "message": "install_id and Bearer token required"}
        ), 401

    fields = _apply_info(body)
    with _connect() as conn:
        row = conn.execute(
            "SELECT install_id, token_hash FROM installs WHERE install_id = ?",
            (install_id,),
        ).fetchone()
        if row is None:
            _update_install(conn, install_id, token_hash, fields, is_new=True)
            _issue_trial_if_needed(conn, install_id)
            return jsonify(
                {
                    "status": "ok",
                    "registered": True,
                    "license": _license_dict(_license_row(conn, install_id)),
                }
            )
        if not hmac.compare_digest(row["token_hash"], token_hash):
            return jsonify(
                {"status": "error", "message": "invalid token for install_id"}
            ), 403
        _update_install(conn, install_id, token_hash, fields, is_new=False)
        return jsonify({"status": "ok", "registered": False})


@app.route("/api/telemetry", methods=["POST"])
def api_telemetry():
    if request.content_length and request.content_length > MAX_JSON:
        return jsonify({"status": "error", "message": "payload too large"}), 413
    body = _json_body()
    install_id, token_hash = _auth_install(body)
    if not install_id or not token_hash:
        return jsonify(
            {"status": "error", "message": "install_id and Bearer token required"}
        ), 401

    fields = _apply_info(body)
    with _connect() as conn:
        row = conn.execute(
            "SELECT token_hash FROM installs WHERE install_id = ?", (install_id,)
        ).fetchone()
        if row is None:
            return jsonify({"status": "error", "message": "not registered"}), 404
        if not hmac.compare_digest(row["token_hash"], token_hash):
            return jsonify(
                {"status": "error", "message": "invalid token for install_id"}
            ), 403
        _update_install(conn, install_id, token_hash, fields, is_new=False)
        return jsonify(
            {"status": "ok", "license": _license_dict(_license_row(conn, install_id))}
        )


@app.route("/api/license")
def api_license():
    """Return the signed license for the authenticated install."""
    install_id, token_hash = _auth_install({})
    if not install_id or not token_hash:
        return jsonify(
            {"status": "error", "message": "install_id and Bearer token required"}
        ), 401
    with _connect() as conn:
        row = conn.execute(
            "SELECT token_hash FROM installs WHERE install_id = ?", (install_id,)
        ).fetchone()
        if row is None:
            return jsonify({"status": "error", "message": "not registered"}), 404
        if not hmac.compare_digest(row["token_hash"], token_hash):
            return jsonify(
                {"status": "error", "message": "invalid token for install_id"}
            ), 403
        license_obj = _license_dict(_license_row(conn, install_id))
    return jsonify({"status": "ok", "install_id": install_id, "license": license_obj})


@app.route("/")
def dashboard():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    if not ADMIN_PASSWORD:
        app.logger.warning("ADMIN_PASSWORD not set — dashboard is locked down (401)")
    return render_template("dashboard.html", csrf_token=_csrf_token())


@app.route("/api/installs")
def api_installs():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM installs ORDER BY last_seen DESC").fetchall()
        installs = []
        for r in rows:
            item = _row_to_dict(r)
            item["license"] = _license_dict(_license_row(conn, r["install_id"]))
            installs.append(item)
    return jsonify({"installs": installs, "now": _now()})


def _web_expires_at(form) -> tuple[str, str | None]:
    """Resolve expires_at from the issue form. Returns (expires_at, error)."""
    expires = _clean(form.get("expires"), 12)
    days = form.get("days")
    days_int = None
    if days not in (None, ""):
        try:
            days_int = int(days)
        except (TypeError, ValueError):
            return None, "days must be an integer"
        if days_int <= 0 or days_int > 3650:
            return None, "days must be between 1 and 3650"
    if expires:
        return expires + "T00:00:00Z", None
    n = days_int if days_int is not None else 365
    return (
        (datetime.now(timezone.utc) + timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        None,
    )


@app.route("/license/issue", methods=["POST"])
def web_license_issue():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    csrf = _csrf_guard()
    if csrf is not None:
        return csrf
    install_id = _clean(request.form.get("install_id"), 100)
    plan = request.form.get("plan")
    if plan not in ("full", "trial"):
        return jsonify(
            {"status": "error", "message": "plan must be 'full' or 'trial'"}
        ), 400
    if not install_id:
        return jsonify({"status": "error", "message": "install_id required"}), 400
    expires_at, error = _web_expires_at(request.form)
    if error:
        return jsonify({"status": "error", "message": error}), 400
    with _connect() as conn:
        install = conn.execute(
            "SELECT install_id FROM installs WHERE install_id = ?", (install_id,)
        ).fetchone()
        if install is None:
            return jsonify(
                {"status": "error", "message": "install not registered"}
            ), 404
        try:
            lic = _issue_license(conn, install_id, plan=plan, expires_at=expires_at)
        except FileNotFoundError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "license": lic})


@app.route("/license/revoke", methods=["POST"])
def web_license_revoke():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    csrf = _csrf_guard()
    if csrf is not None:
        return csrf
    install_id = _clean(request.form.get("install_id"), 100)
    if not install_id:
        return jsonify({"status": "error", "message": "install_id required"}), 400
    with _connect() as conn:
        changed = _revoke_license(conn, install_id)
    return jsonify({"status": "ok", "revoked": changed > 0})


@app.route("/license/delete", methods=["POST"])
def web_license_delete():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    csrf = _csrf_guard()
    if csrf is not None:
        return csrf
    install_id = _clean(request.form.get("install_id"), 100)
    if not install_id:
        return jsonify({"status": "error", "message": "install_id required"}), 400
    with _connect() as conn:
        ins, lic = _delete_install(conn, install_id)
    if ins == 0 and lic == 0:
        return jsonify({"status": "error", "message": "install not found"}), 404
    return jsonify({"status": "ok", "deleted": True})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": _now()})


_init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
