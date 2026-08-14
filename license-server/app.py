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
    LICENSE_ENV          development | production (default development)
    LICENSE_ADMIN_SECRET REQUIRED in production — Flask session/CSRF secret
    LICENSE_DB_PATH      sqlite file path (default ./data/license.db)
    ADMIN_USER           basic-auth user for the dashboard
    ADMIN_PASSWORD       basic-auth password for the dashboard
    TRIAL_DAYS           trial license length in days for new installs (default 30)
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, render_template, request, session

import ipintel
import licensing

app = Flask(__name__)
LICENSE_ENV = os.environ.get("LICENSE_ENV", "development")
app.secret_key = os.environ.get("LICENSE_ADMIN_SECRET")
if LICENSE_ENV == "production":
    if not app.secret_key:
        raise RuntimeError(
            "LICENSE_ADMIN_SECRET is REQUIRED in production (LICENSE_ENV=production). "
            'Generate with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
if not app.secret_key:
    # Dev/test only: random fallback (sessions/CSRF are lost on restart).
    app.secret_key = os.urandom(32).hex()

DB_PATH = os.environ.get(
    "LICENSE_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "license.db")
)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

MAX_TEXT = 500
MAX_JSON = 8192
PURGE_INTERVAL_SECONDS = int(os.environ.get("LICENSE_PURGE_INTERVAL_SECONDS", "3600"))
_last_purge = 0.0


def _trusted_proxy_networks():
    configured = os.environ.get("LICENSE_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128")
    networks = []
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise RuntimeError(f"Invalid LICENSE_TRUSTED_PROXY_CIDRS entry: {value!r}")
    return tuple(networks)


TRUSTED_PROXY_NETWORKS = _trusted_proxy_networks()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table: str, column: str, decl: str) -> None:
    """Add a column if it does not exist yet (CREATE TABLE IF NOT EXISTS cannot)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError as exc:
            # Gunicorn workers can initialize the app concurrently. If another
            # worker won the ALTER race, the desired schema is already present.
            if "duplicate column name" not in str(exc).lower():
                raise


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
                last_ip         TEXT,
                ip_intel        TEXT,
                ip_intel_at     TEXT,
                last_http       TEXT,
                last_http_at    TEXT,
                ip_check_at     TEXT
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ip_intel (
                ip           TEXT PRIMARY KEY,
                data         TEXT NOT NULL,
                queried_at   REAL NOT NULL,
                ttl_seconds  REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor       TEXT NOT NULL,
                action      TEXT NOT NULL,
                resource    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        # Migrate pre-existing installs tables (CREATE TABLE IF NOT EXISTS never alters).
        _ensure_column(conn, "installs", "ip_intel", "TEXT")
        _ensure_column(conn, "installs", "ip_intel_at", "TEXT")
        _ensure_column(conn, "installs", "last_http", "TEXT")
        _ensure_column(conn, "installs", "last_http_at", "TEXT")
        _ensure_column(conn, "installs", "ip_check", "TEXT")
        _ensure_column(conn, "installs", "ip_check_at", "TEXT")


def _retention_days(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def purge_sensitive_data(conn) -> dict[str, int]:
    """Purge IP-derived fields and cache rows according to configured retention."""
    counts = {}
    for column, env_name, default in (
        ("ip_intel", "LICENSE_IP_INTEL_RETENTION_DAYS", 30),
        ("last_http", "LICENSE_HTTP_RETENTION_DAYS", 7),
        ("ip_check", "LICENSE_IP_CHECK_RETENTION_DAYS", 30),
    ):
        days = _retention_days(env_name, default)
        cursor = conn.execute(
            f"UPDATE installs SET {column} = NULL "
            f", {column}_at = NULL WHERE {column} IS NOT NULL "
            f"AND datetime(COALESCE({column}_at, last_seen)) < datetime('now', ?)",
            (f"-{days} days",),
        )
        counts[column] = cursor.rowcount
    cache_days = _retention_days("LICENSE_IP_CACHE_RETENTION_DAYS", 30)
    cursor = conn.execute(
        "DELETE FROM ip_intel WHERE queried_at < ?",
        (time.time() - cache_days * 86400,),
    )
    counts["ip_intel_cache"] = cursor.rowcount
    audit_days = _retention_days("LICENSE_ADMIN_AUDIT_RETENTION_DAYS", 365)
    cursor = conn.execute(
        "DELETE FROM admin_audit WHERE datetime(created_at) < datetime('now', ?)",
        (f"-{audit_days} days",),
    )
    counts["admin_audit"] = cursor.rowcount
    return counts


def _maybe_purge() -> None:
    global _last_purge
    now = time.monotonic()
    if now - _last_purge < PURGE_INTERVAL_SECONDS:
        return
    with _connect() as conn:
        purge_sensitive_data(conn)
    _last_purge = now


def _audit_admin(action: str, resource: str) -> None:
    auth = request.authorization
    actor = _clean(auth.username if auth else ADMIN_USER, 100) or "unknown"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO admin_audit (actor, action, resource, created_at) VALUES (?, ?, ?, ?)",
            (actor, action, _clean(resource, 200) or "unknown", _now()),
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
    intel = row["ip_intel"]
    http = row["last_http"]
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
        "ip_intel": json.loads(intel) if intel else None,
        "last_http": json.loads(http) if http else None,
        "ip_check": json.loads(row["ip_check"]) if row["ip_check"] else None,
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


@app.before_request
def _privacy_maintenance():
    _maybe_purge()


def _apply_info(body) -> dict:
    info = body.get("info") or {}
    if not isinstance(info, dict):
        info = {}
    fields = {
        "hostname": _clean(info.get("hostname")),
        "public_ip": _clean(info.get("public_ip"), 64),
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


def _client_ip() -> str:
    """Return the client IP, trusting forwarding headers only from a proxy."""
    peer = request.remote_addr
    try:
        peer_is_proxy = peer and any(
            ipaddress.ip_address(peer) in network for network in TRUSTED_PROXY_NETWORKS
        )
    except ValueError:
        peer_is_proxy = False
    if not peer_is_proxy:
        return _clean(peer, 64)

    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return _clean(first, 64)
    return _clean(peer, 64)


def _http_metadata() -> str | None:
    """Compact JSON snapshot of the request metadata we do not store elsewhere."""
    snapshot = {
        "ua": _clean(request.headers.get("User-Agent"), 300),
        "accept_language": _clean(request.headers.get("Accept-Language"), 200),
        "protocol": request.environ.get("SERVER_PROTOCOL") or "http/1.1",
    }
    snapshot = {key: value for key, value in snapshot.items() if value}
    if not snapshot:
        return None
    return json.dumps(snapshot, separators=(",", ":"))


def _ip_check(client_ip: str, reported: str | None) -> str | None:
    """Cross-check the client-reported public IP against the observed one.

    Flags: "ok" (same), "mismatch" (reported public IP differs — likely a VPN
    or proxy in between), "nat" (reported IP is a private/RFC1918 range), or
    "none" when the client did not report one.
    """
    if not reported or reported in ("0.0.0.0", "::", "none"):
        return json.dumps({"flag": "none"}, separators=(",", ":"))
    if reported == client_ip:
        return json.dumps({"flag": "ok", "reported": reported}, separators=(",", ":"))
    if not ipintel._is_lookupable(reported):
        return json.dumps(
            {"flag": "nat", "reported": reported, "actual": client_ip},
            separators=(",", ":"),
        )
    return json.dumps(
        {"flag": "mismatch", "reported": reported, "actual": client_ip},
        separators=(",", ":"),
    )


def _update_install(conn, install_id, token_hash, fields, is_new):
    now = _now()
    client_ip = _client_ip()
    try:
        ip_intel = ipintel.enrich(conn, client_ip)
    except Exception:
        app.logger.warning(
            "IP-enrichment lookup failed for %s", client_ip, exc_info=True
        )
        ip_intel = {}
    data = dict(fields)
    data["last_seen"] = now
    data["last_ip"] = _clean(client_ip, 64)
    data["ip_intel"] = json.dumps(ip_intel, separators=(",", ":"))
    data["ip_intel_at"] = now
    data["last_http"] = _http_metadata()
    data["last_http_at"] = now
    data["ip_check"] = _ip_check(client_ip, data.get("public_ip"))
    data["ip_check_at"] = now
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
    _audit_admin("view", "dashboard")
    return render_template("dashboard.html", csrf_token=_csrf_token())


@app.route("/api/installs")
def api_installs():
    guard = _basic_auth_guard()
    if guard is not None:
        return guard
    _audit_admin("export", "installs")
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
