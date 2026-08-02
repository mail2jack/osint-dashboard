"""
Iveras License & Telemetry Server — fase 1.

Central registry for all OSINT Dashboard installs:

    POST /api/register   — register a new install (idempotent)
    POST /api/telemetry  — daily heartbeat + updated system info
    GET  /               — registry dashboard (HTTP Basic Auth)
    GET  /api/installs   — registry as JSON (HTTP Basic Auth)

Storage: SQLite via the stdlib sqlite3 module. Dependencies: Flask + gunicorn.

Runtime config (env vars):
    LICENSE_DB_PATH     sqlite file path (default ./data/license.db)
    ADMIN_USER          basic-auth user for the dashboard
    ADMIN_PASSWORD      basic-auth password for the dashboard
"""

import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

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


def _json_body():
    try:
        return request.get_json(silent=True) or {}
    except Exception:
        return {}


def _auth_install(body) -> tuple[str | None, str | None]:
    install_id = _clean(body.get("install_id"), 100)
    auth = request.headers.get("Authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = auth[len("Bearer ") :].strip()
    if not install_id or not token:
        return None, None
    return install_id, _token_hash(token)


def _basic_auth_ok() -> bool:
    if not ADMIN_PASSWORD:
        return True
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
            return jsonify({"status": "ok", "registered": True})
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
        return jsonify({"status": "ok"})


@app.route("/")
def dashboard():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    if not ADMIN_PASSWORD:
        app.logger.warning("ADMIN_PASSWORD not set — dashboard is unauthenticated")
    return render_template("dashboard.html")


@app.route("/api/installs")
def api_installs():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM installs ORDER BY last_seen DESC").fetchall()
    return jsonify({"installs": [_row_to_dict(r) for r in rows], "now": _now()})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": _now()})


_init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
