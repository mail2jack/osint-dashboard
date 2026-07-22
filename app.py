# Iveras OSINT Dashboard
# Copyright (C) 2026  Gast
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import re
import secrets
import time
import logging
import threading
import uuid as uuid_mod
import contextlib

from flask import Flask, redirect, request, g
from dotenv import load_dotenv

load_dotenv()

from cms.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# CSS/JS cache busting — use build version when available, fallback to mtime
_USE_BUNDLE = os.path.exists(
    os.path.join(os.path.dirname(__file__), "static", "dist", "bundle.min.css")
)
if _USE_BUNDLE:
    _CSS_VERSION_FILE = os.path.join(
        os.path.dirname(__file__), "static", "dist", ".css_version"
    )
    CSS_VERSION = (
        open(_CSS_VERSION_FILE).read().strip()
        if os.path.exists(_CSS_VERSION_FILE)
        else str(
            int(
                os.path.getmtime(
                    os.path.join(
                        os.path.dirname(__file__), "static", "dist", "bundle.min.css"
                    )
                )
            )
        )
    )
else:
    _CSS_PATHS = [
        os.path.join(os.path.dirname(__file__), d)
        for d in ("static/css/base.css", "static/css/cms-professional.css")
    ]
    CSS_VERSION = (
        str(int(max(os.path.getmtime(p) for p in _CSS_PATHS if os.path.exists(p))))
        if any(os.path.exists(p) for p in _CSS_PATHS)
        else "1"
    )


def _init_sentry(dsn: str) -> None:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                FlaskIntegration(),
                SqlalchemyIntegration(),
            ],
            send_default_pii=True,
            enable_logs=True,
            traces_sample_rate=float(
                os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0")
            ),
            profile_session_sample_rate=float(
                os.environ.get("SENTRY_PROFILE_SAMPLE_RATE", "1.0")
            ),
            profile_lifecycle=os.environ.get("SENTRY_PROFILE_LIFECYCLE", "trace"),
            environment=os.environ.get("FLASK_ENV", "development"),
        )
        logger.info("Sentry initialized (DSN: ...%s)", dsn[-12:])
    except Exception as e:
        logger.warning("Failed to initialize Sentry: %s", e)


# Sentry — opt-in via env var (before app is created)
_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    _init_sentry(_sentry_dsn)


app = Flask(__name__)


@app.before_request
def add_request_id():
    request.request_id = (
        request.headers.get("X-Request-ID") or uuid_mod.uuid4().hex[:12]
    )
    g.request_id = request.request_id
    g.csp_nonce = secrets.token_hex(16)
    g.css_version = CSS_VERSION
    g.use_bundle = _USE_BUNDLE
    # Clear any stale aborted transaction from the connection pool
    from cms.models import db

    with contextlib.suppress(Exception):
        db.session.rollback()


@app.before_request
def set_tenant_context():
    """Set RLS tenant context for PostgreSQL Row-Level Security."""
    from flask_login import current_user
    from sqlalchemy import text
    from cms.models import db as _db

    if current_user.is_authenticated:
        tid = current_user.tenant_id
        if current_user.is_super_admin:
            from flask import session as _session

            tid = _session.get("switched_tenant_id") or tid
        g.tenant_id = tid
        try:
            _db.session.execute(
                text("SET app.tenant_id = :tid"),
                {"tid": tid},
            )
            # Only bypass RLS when NOT switched (super-admin viewing own tenant)
            is_switched = current_user.is_super_admin and _session.get(
                "switched_tenant_id"
            )
            if current_user.is_super_admin and not is_switched:
                _db.session.execute(text("SET app.bypass_rls = 'true'"))
            else:
                _db.session.execute(text("SET app.bypass_rls = 'false'"))
        except Exception:
            _db.session.rollback()
    else:
        g.tenant_id = None


@app.before_request
def redirect_https():
    """Redirect HTTP to HTTPS when behind a reverse proxy."""
    if not app.debug and request.headers.get("X-Forwarded-Proto", "").lower() == "http":
        return redirect(request.url.replace("http://", "https://", 1), 301)


# Warn if SQLite is in use — RLS tenant isolation only works with PostgreSQL
if "sqlite" in str(app.config.get("SQLALCHEMY_DATABASE_URI", "")):
    import logging as _log

    _log.warning(
        "SQLite detected — multi-tenant RLS isolation is NOT active. "
        "Use PostgreSQL for production multi-tenant deployments."
    )


# Load security and application configuration based on FLASK_ENV
from cms.config import get_config

app.config.from_object(get_config())

# CORS — allow same-origin by default, configurable via env var
from flask_cors import CORS

_cors_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5000,http://127.0.0.1:5000,http://localhost:5001,http://localhost:5002",
)
_cors_origins = [o.strip() for o in _cors_origins.split(",")]
CORS(app, origins=_cors_origins, supports_credentials=True)

# Server-side sessions — Redis when available, fallback to filesystem
from flask_session import Session

_redis_url = os.environ.get("REDIS_URL")
if _redis_url:
    try:
        import redis as _redis_mod

        _redis_client = _redis_mod.from_url(_redis_url)
        _redis_client.ping()
        app.config["SESSION_TYPE"] = "redis"
        app.config["SESSION_REDIS"] = _redis_client
        app.config["SESSION_PERMANENT"] = True
        app.config["SESSION_SERIALIZATION_FORMAT"] = "json"
        logger.info("Session backend: Redis (%s)", _redis_url)
    except Exception:
        logger.warning("Redis unavailable, falling back to filesystem sessions")
        _redis_url = None

if not _redis_url:
    from cachelib.file import FileSystemCache

    _session_dir = os.path.join(os.path.dirname(__file__), "flask_session")
    app.config.setdefault("SESSION_TYPE", "cachelib")
    app.config.setdefault("SESSION_PERMANENT", True)
    app.config.setdefault("SESSION_SERIALIZATION_FORMAT", "json")
    app.config["SESSION_CACHELIB"] = FileSystemCache(
        cache_dir=_session_dir, threshold=5000, default_timeout=28800
    )

Session(app)

# =============================================================================
# Case Management System (CMS) Integration
# =============================================================================

# Encryption key: env var → .cms_key file → auto-generate + persist
if not os.environ.get("CMS_ENCRYPTION_KEY"):
    key_file = os.path.join(os.path.dirname(__file__), ".cms_key")
    if os.path.exists(key_file):
        with open(key_file) as f:
            os.environ["CMS_ENCRYPTION_KEY"] = f.read().strip()
        logger.info("Loaded CMS encryption key from .cms_key file")
    else:
        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = new_key
        try:
            with open(key_file, "w") as f:
                f.write(new_key)
            os.chmod(key_file, 0o600)
            logger.warning(
                "Generated new CMS encryption key — saved to .cms_key (chmod 600). "
                "Set CMS_ENCRYPTION_KEY env var or keep .cms_key for persistence."
            )
        except Exception as e:
            logger.error(f"Failed to persist encryption key to .cms_key: {e}")

# Set secret key for sessions (required for CMS) — persisted across restarts
if not app.config.get("SECRET_KEY"):
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        key_file = os.path.join(os.path.dirname(__file__), ".secret_key")
        if os.path.exists(key_file):
            with open(key_file) as f:
                secret = f.read().strip()
            logger.info("Loaded SECRET_KEY from .secret_key file")
        else:
            if os.environ.get("FLASK_ENV") == "production":
                raise RuntimeError(
                    "SECRET_KEY environment variable is REQUIRED in production."
                )
            import secrets

            secret = secrets.token_hex(32)
            try:
                with open(key_file, "w") as f:
                    f.write(secret)
                os.chmod(key_file, 0o600)
                logger.warning(
                    "Generated new SECRET_KEY — saved to .secret_key (chmod 600). "
                    "Set SECRET_KEY env var or keep .secret_key for persistence."
                )
            except Exception as e:
                logger.error(f"Failed to persist SECRET_KEY to .secret_key: {e}")
    app.config["SECRET_KEY"] = secret

# Database: PostgreSQL if DATABASE_URL is set, fallback to SQLite
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Resolve relative SQLite paths to absolute to prevent mismatch with Alembic
    if database_url.startswith("sqlite:///") and not database_url.startswith(
        "sqlite:////"
    ):
        rel_path = database_url[len("sqlite:///") :]
        abs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), rel_path))
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{abs_path}"
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    CMS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "cms.db"))
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{CMS_DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# SQLite cannot use connection pooling — override config defaults
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
    app.config.pop("SQLALCHEMY_ENGINE_OPTIONS", None)

# Graceful PostgreSQL fallback: if Postgres is unreachable, use SQLite
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql"):
    try:
        from sqlalchemy import create_engine

        ssl_mode = app.config.get("DB_SSL_MODE", "prefer")
        engine = create_engine(
            app.config["SQLALCHEMY_DATABASE_URI"],
            connect_args={"connect_timeout": 2, "sslmode": ssl_mode},
        )
        engine.connect().close()
        engine.dispose()
    except Exception as e:
        logger.warning("PostgreSQL unreachable (%s), falling back to SQLite", e)
        CMS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "cms.db"))
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{CMS_DB_PATH}"
        app.config.pop("SQLALCHEMY_ENGINE_OPTIONS", None)

# Initialize CMS using create_cms_module
from cms import create_cms_module, csrf

create_cms_module(app)
logger.info("CMS initialized successfully")

# Stripe webhook blueprint — registered after CMS to be CSRF-exempt
from cms.routes.stripe_billing import stripe_bp

app.register_blueprint(stripe_bp)

# Internationalization (i18n) via Flask-Babel
from cms.i18n import init_i18n

init_i18n(app)
logger.info("i18n initialized (EN default, NL optional)")

# Sentry fallback: check Setting table if no env var was set
if not _sentry_dsn:
    try:
        from cms.models import Setting

        _setting_dsn = Setting.get("sentry_dsn")
        if _setting_dsn:
            _init_sentry(_setting_dsn)
    except Exception:
        pass

# Centralized API error handlers
from cms.api_errors import register_error_handlers

register_error_handlers(app)

# Prometheus /metrics endpoint
from cms.metrics import register_metrics_route

register_metrics_route(app)

# =============================================================================
# Template Filters — registered on the app (used by CMS templates)
# =============================================================================


@app.template_filter("urlize_target")
def urlize_target_filter(text):
    if not text:
        return ""
    from html import escape

    text = escape(str(text))
    url_pattern = re.compile(r'(https?:\/\/[^\s<>"\'(){}|\\^`\[\]]+)')

    def make_url(m):
        url = m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener">{url[:80]}</a>'

    return url_pattern.sub(make_url, text)


PLATFORM_COLORS = {
    "instagram": "#E4405F",
    "facebook": "#1877F2",
    "twitter": "#1DA1F2",
    "linkedin": "#0A66C2",
    "github": "#333333",
    "youtube": "#FF0000",
    "tiktok": "#000000",
    "snapchat": "#FFFC00",
    "reddit": "#FF4500",
    "pinterest": "#E60023",
    "telegram": "#2AABEE",
    "whatsapp": "#25D366",
    "signal": "#3A76F0",
    "discord": "#5865F2",
    "medium": "#000000",
    "tumblr": "#35465C",
    "twitch": "#9146FF",
    "vimeo": "#1AB7EA",
    "x": "#000000",
    "threads": "#000000",
    "mastodon": "#6364FF",
    "bluesky": "#0085FF",
    "strava": "#FC4C02",
    "flickr": "#0063DC",
}


@app.template_filter("platform_name")
def platform_name_filter(url):
    if not url:
        return ""
    url = str(url).lower()
    platform_names = {
        "instagram.com": "Instagram",
        "facebook.com": "Facebook",
        "fb.com": "Facebook",
        "twitter.com": "Twitter",
        "x.com": "X (Twitter)",
        "linkedin.com": "LinkedIn",
        "github.com": "GitHub",
        "youtube.com": "YouTube",
        "youtu.be": "YouTube",
        "tiktok.com": "TikTok",
        "snapchat.com": "Snapchat",
        "reddit.com": "Reddit",
        "pinterest.com": "Pinterest",
        "telegram.org": "Telegram",
        "t.me": "Telegram",
        "whatsapp.com": "WhatsApp",
        "signal.org": "Signal",
        "discord.com": "Discord",
        "discord.gg": "Discord",
        "medium.com": "Medium",
        "tumblr.com": "Tumblr",
        "twitch.tv": "Twitch",
        "vimeo.com": "Vimeo",
        "mastodon.social": "Mastodon",
        "bsky.app": "Bluesky",
        "threads.net": "Threads",
        "strava.com": "Strava",
        "flickr.com": "Flickr",
        "blogspot.com": "Blogger",
    }
    for domain, name in platform_names.items():
        if domain in url:
            return name
    return url.split("/")[2] if url.startswith("http") else url


@app.template_filter("platform_color")
def platform_color_filter(url):
    if not url:
        return "#666"
    url = str(url).lower()
    for key, color in PLATFORM_COLORS.items():
        if key in url:
            return color
    return "#666"


@app.template_filter("result_link")
def result_link_filter(value, group=None):
    if not value:
        return ""
    from html import escape

    text = escape(str(value))
    if text.startswith(("http://", "https://")):
        return f'<a href="{text}" target="_blank" rel="noopener">{text}</a>'
    if "@" in text and "." in text.split("@")[-1]:
        return f'<a href="mailto:{text}">{text}</a>'
    return text


@app.template_filter("redact_for_viewer")
def redact_for_viewer_filter(value):
    """Redact sensitive values when the current user is a viewer."""
    if not value:
        return value
    from flask_login import current_user

    if current_user and current_user.is_authenticated and current_user.role == "viewer":
        s = str(value)
        # Phone numbers: show last 4 digits
        if any(c.isdigit() for c in s) and ("+" in s or any(c.isdigit() for c in s)):
            digits = "".join(c for c in s if c.isdigit())
            if len(digits) >= 4:
                return s[:-4] + "****" if len(s) > 4 else "****"
        # Email: show first char + domain
        if "@" in s:
            local, domain = s.split("@", 1)
            if local:
                return local[0] + "***@" + domain
        # General: show first 3 + last 3 chars
        if len(s) > 12:
            return s[:3] + "..." + s[-3:]
        if len(s) > 6:
            return s[:2] + "..." + s[-2:]
        return s[0] + "..." if len(s) > 1 else "***"
    return value


# =============================================================================
# End CMS Integration
# =============================================================================


# Context processor count caches (30s TTL)
_count_cache: dict[str, tuple[float, int]] = {}
_count_cache_lock = threading.Lock()


def _cached_count(key: str, ttl: float, factory) -> int:
    now = time.time()
    with _count_cache_lock:
        cached = _count_cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1]
        try:
            val = factory()
            _count_cache[key] = (now, val)
            return val
        except Exception:
            return 0


# Context Processor — inject version + SF health + help topic into all templates
@app.context_processor
def inject_globals():
    from version import get_version

    _nonce = getattr(g, "csp_nonce", secrets.token_hex(16))
    g.csp_nonce = _nonce
    ctx = {
        "current_version": get_version(),
        "csp_nonce": _nonce,
    }
    # Notification count for bell icon (cached 30s)
    try:
        from cms.models import Notification
        from flask_login import current_user

        if current_user and current_user.is_authenticated:

            def _count_notifications():
                return Notification.query.filter_by(
                    user_id=current_user.id, is_read=False
                ).count()

            ctx["notification_count"] = _cached_count(
                f"notif_{current_user.id}", 30.0, _count_notifications
            )
        else:
            ctx["notification_count"] = 0
    except Exception:
        from cms.models import db

        db.session.rollback()
        ctx["notification_count"] = 0
    try:
        from cms.setting_cache import cached_setting_get

        sf_health = cached_setting_get("spiderfoot_health", "")
        sf_last_ok = cached_setting_get("spiderfoot_last_ok", "")
        ctx["spiderfoot_health"] = sf_health
        ctx["spiderfoot_last_ok"] = sf_last_ok
    except Exception:
        from cms.models import db

        db.session.rollback()
        ctx["spiderfoot_health"] = ""
        ctx["spiderfoot_last_ok"] = ""
    # Check for recent login anomalies (cached 30s)
    try:
        from flask import request
        from cms.models import LoginLog, db
        from datetime import datetime, timezone, timedelta
        from flask_login import current_user

        # Only check if user is authenticated and admin
        if (
            current_user
            and current_user.is_authenticated
            and hasattr(current_user, "role")
            and current_user.role == "admin"
        ):

            def _count_anomalies():
                recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                return LoginLog.query.filter(
                    LoginLog.is_anomaly == True,
                    LoginLog.created_at >= recent_cutoff,
                ).count()

            ctx["login_anomaly_count"] = _cached_count(
                "login_anomalies", 30.0, _count_anomalies
            )
        else:
            ctx["login_anomaly_count"] = 0
    except Exception:
        from cms.models import db

        db.session.rollback()
        ctx["login_anomaly_count"] = 0

    # Session expiry time for timeout warning
    try:
        from datetime import timedelta
        from flask import session

        if session.permanent:
            lifetime = app.permanent_session_lifetime
            ctx["session_lifetime_seconds"] = int(lifetime.total_seconds())
        else:
            ctx["session_lifetime_seconds"] = 28800  # 8h default
    except Exception:
        ctx["session_lifetime_seconds"] = 28800

    # Derive help topic from request endpoint
    try:
        from flask import request

        if request and request.endpoint:
            ep = request.endpoint
            if ep.startswith("cms."):
                topic = ep.split(".", 1)[1]
                # Map endpoint names to help topic names
                topic_map = {
                    "dashboard": "dashboard",
                    "cases_crud": "cases",
                    "cases_state": "cases",
                    "cases_subjects": "cases",
                    "cases_reports": "cases",
                    "clients_crud": "clients",
                    "clients_archive": "clients",
                    "subjects_list": "subjects",
                    "subjects_crud": "subjects",
                    "subjects_faces": "subjects",
                    "subjects_rel": "subjects",
                    "spiderfoot": "spiderfoot",
                    "settings": "settings",
                    "osint_search": "search",
                    "search": "search",
                }
                ctx["help_topic"] = topic_map.get(topic, "general")
            else:
                ctx["help_topic"] = "general"
        else:
            ctx["help_topic"] = "general"
    except Exception:
        ctx["help_topic"] = "general"
    from flask_babel import gettext as _t
    from cms.i18n import get_locale as _get_locale

    ctx["_"] = _t
    ctx["current_locale"] = _get_locale()
    ctx["now"] = lambda: datetime.now(timezone.utc)

    # Unacknowledged announcements for mandatory popup (cached 30s)
    try:
        from cms.models import Announcement, AnnouncementAck, db
        from flask_login import current_user
        from datetime import datetime, timezone

        if current_user and current_user.is_authenticated:

            def _count_announcements():
                now = datetime.now(timezone.utc)
                return (
                    Announcement.query.outerjoin(
                        AnnouncementAck,
                        db.and_(
                            AnnouncementAck.announcement_id == Announcement.id,
                            AnnouncementAck.user_id == current_user.id,
                        ),
                    )
                    .filter(
                        Announcement.is_active == True,
                        Announcement.starts_at <= now,
                        (Announcement.expires_at.is_(None))
                        | (Announcement.expires_at > now),
                        AnnouncementAck.id.is_(None),
                    )
                    .all()
                )

            _announcements = _cached_count(
                f"announcements_{current_user.id}", 30.0, _count_announcements
            )
            ctx["unacknowledged_announcements"] = (
                [a.to_dict() for a in _announcements] if _announcements else []
            )
        else:
            ctx["unacknowledged_announcements"] = []
    except Exception:
        from cms.models import db

        db.session.rollback()
        ctx["unacknowledged_announcements"] = []

    # Switched tenant context for super-admin
    try:
        from flask import session as _ctx_session
        from cms.models import Tenant

        tid = _ctx_session.get("switched_tenant_id")
        if tid:
            _switched = db.session.get(Tenant, tid)
            ctx["switched_tenant"] = _switched.to_dict() if _switched else None
        else:
            ctx["switched_tenant"] = None
    except Exception:
        ctx["switched_tenant"] = None

    return ctx


# =============================================================================
# App-level Routes — imported from route modules
# =============================================================================

from cms.routes.app_bp import app_routes_bp

app.register_blueprint(app_routes_bp)

from cms.routes.system_app import register_system_routes

register_system_routes(app)

# Workflow sandbox blueprint — superadmin only, separate SQLite DB
from cms.workflow import workflow_bp

app.register_blueprint(workflow_bp)


@app.route("/lang/<lang>")
def set_lang(lang: str):
    from flask import redirect, session, request

    if lang in ("nl", "en", "de", "fr"):
        session["lang"] = lang
    return redirect(request.referrer or "/")


logger.info("App-level routes registered")

# ── Security headers ──────────────────────────────────────────────────────────


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    nonce = getattr(g, "csp_nonce", "")
    # 'unsafe-eval' required by TensorFlow.js (face-api) for WebGL backend
    csp = (
        f"default-src 'self'; "
        f"script-src 'self' 'unsafe-eval' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        f"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data: https:; "
        f"connect-src 'self' https:; "
        f"frame-src 'none'; "
        f"object-src 'none'; "
        f"report-uri /csp-report"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # Long-lived cache for static assets
    if response.content_type and response.content_type.startswith(
        ("text/css", "application/javascript", "image/")
    ):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # Only set HSTS for non-localhost
    host = request.host.split(":")[0] if request.host else ""
    if host and host != "localhost" and host != "127.0.0.1":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


# Response compression (brotli/gzip) — initialized last
from flask_compress import Compress

# Lower min size since brotli is efficient
app.config["COMPRESS_REGISTER"] = True
app.config["COMPRESS_ALGORITHM"] = "br"
app.config["COMPRESS_BR_LEVEL"] = 4
app.config["COMPRESS_MIN_SIZE"] = 500
Compress(app)


# =============================================================================
# CLI commands
# =============================================================================


@app.cli.command("aggregate-usage")
def aggregate_usage():
    """Aggregate daily usage metrics for all active tenants."""
    from cms.aggregation import aggregate_yesterday, check_and_alert_usage_limits

    count = aggregate_yesterday()
    alerts = check_and_alert_usage_limits()
    print(f"Aggregated usage for {count} tenants")
    if alerts:
        print(f"Triggered {len(alerts)} usage alerts")
    else:
        print("No usage alerts triggered")


@app.cli.command("purge-expired-tenants")
def purge_expired_tenants_cli():
    """Hard-delete tenant data for tenants past their retention grace period."""
    from cms.data_retention import purge_expired_tenants

    count = purge_expired_tenants()
    print(f"Purged {count} expired tenant(s)")


@app.cli.command("purge-expired-tenants-dry-run")
def purge_expired_tenants_dry_run():
    """Dry-run: show what would be purged without deleting anything."""
    from cms.data_retention import purge_expired_tenants

    count = purge_expired_tenants(dry_run=True)
    print(f"Would purge {count} expired tenant(s) (dry run)")


@app.cli.command("check-overdue-invoices")
def check_overdue_invoices():
    """Mark sent invoices past due_date as overdue and notify."""
    from datetime import date
    from cms.models import db, Invoice, Notification, User

    today = date.today()
    overdue = Invoice.query.filter(
        Invoice.status == "sent",
        Invoice.due_date < today,
        Invoice.is_deleted == False,
    ).all()

    marked = 0
    for inv in overdue:
        inv.mark_overdue()
        # Notify tenant admins
        admins = User.query.filter(
            User.tenant_id == inv.tenant_id,
            User.is_active == True,
            User.role.in_(["admin", "owner"]),
        ).all()
        for admin in admins:
            n = Notification(
                tenant_id=inv.tenant_id,
                user_id=admin.id,
                category="system",
                title="Invoice overdue",
                message=f"Invoice {inv.invoice_number} is now overdue (due: {inv.due_date}).",
                link=f"/cms/invoices/{inv.id}",
            )
            db.session.add(n)
        marked += 1

    db.session.commit()
    print(f"Marked {marked} invoice(s) as overdue")


@app.route("/csp-report", methods=["POST"])
@csrf.exempt
def csp_report():
    """Accept CSP violation reports."""
    import json

    report = request.get_json(silent=True)
    if report:
        app.logger.warning("CSP violation: %s", json.dumps(report, indent=2))
    return "", 204


@app.cli.command("opsec:check")
def opsec_check():
    """Run OPSEC validation checks (Tor, stealth, audit chain, etc.)."""
    from cms.opsec_check import run_opsec_checks, print_results

    results = run_opsec_checks(verbose=True)
    print_results(results)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
