import os
import re
import secrets
import time
import logging
import threading
import uuid as uuid_mod
import contextlib

from flask import Flask, request, g
from dotenv import load_dotenv

load_dotenv()

from cms.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# CSS cache busting — use file modification time as version
_CSS_PATH = os.path.join(os.path.dirname(__file__), "static", "css", "base.css")
CSS_VERSION = (
    str(int(os.path.getmtime(_CSS_PATH))) if os.path.exists(_CSS_PATH) else "1"
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
            traces_sample_rate=float(
                os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
            ),
            environment=os.environ.get("FLASK_ENV", "development"),
            send_default_pii=False,
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
    # Clear any stale aborted transaction from the connection pool
    from cms.models import db

    with contextlib.suppress(Exception):
        db.session.rollback()


# Load security and application configuration based on FLASK_ENV
from cms.config import get_config

app.config.from_object(get_config())

# CORS — allow same-origin by default, configurable via env var
from flask_cors import CORS

_cors_origins = os.environ.get("CORS_ORIGINS", "*")
if _cors_origins != "*":
    _cors_origins = [o.strip() for o in _cors_origins.split(",")]
CORS(app, origins=_cors_origins, supports_credentials=True)

# Server-side sessions (CacheLib filesystem backend)
from flask_session import Session
from cachelib.file import FileSystemCache

_session_dir = os.path.join(os.path.dirname(__file__), "flask_session")
app.config.setdefault("SESSION_TYPE", "cachelib")
app.config.setdefault("SESSION_PERMANENT", True)
app.config.setdefault("SESSION_SERIALIZATION_FORMAT", "json")
app.config["SESSION_CACHELIB"] = FileSystemCache(
    cache_dir=_session_dir, threshold=5000, default_timeout=28800
)
Session(app)

# Clean up expired session files at startup
_session_cache = app.config["SESSION_CACHELIB"]
with contextlib.suppress(Exception):
    _session_cache._prune()

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

# Set secret key for sessions (required for CMS)
if not app.config.get("SECRET_KEY"):
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if os.environ.get("FLASK_ENV") == "production":
            raise RuntimeError(
                "SECRET_KEY environment variable is REQUIRED in production."
            )
        import secrets

        secret = secrets.token_hex(32)
        import warnings

        warnings.warn(
            "Using randomly generated SECRET_KEY (not persisted). Set SECRET_KEY env var for production!"
        )
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

# Initialize CMS using create_cms_module
from cms import create_cms_module

create_cms_module(app)
logger.info("CMS initialized successfully")

# Internationalization (i18n) via Flask-Babel
from cms.i18n import init_i18n

init_i18n(app)
logger.info("i18n initialized (NL default, EN fallback)")

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
    text = str(text)
    url_pattern = re.compile(r'(https?:\/\/[^\s<>"\'(){}|\\^`\[\]]+)')

    def make_url(m):
        url = m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener">{url[:80]}</a>'

    return url_pattern.sub(make_url, text)


@app.template_filter("result_link")
def result_link_filter(data, type_name):
    from urllib.parse import quote

    if not data:
        return ""
    if type_name == "email" or type_name in ("ip", "domain"):
        return f'<a href="/cms/search?q={quote(data)}&type=all" class="result-link">{data}</a>'
    return str(data)


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

    _nonce = secrets.token_hex(16)
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
    return ctx


# =============================================================================
# App-level Routes — imported from route modules
# =============================================================================

from cms.routes.app_bp import app_routes_bp

app.register_blueprint(app_routes_bp)

from cms.routes.system_app import register_system_routes

register_system_routes(app)


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
        f"object-src 'none'"
    )
    response.headers["Content-Security-Policy"] = csp
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
# Main
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
