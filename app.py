import os
import re
import logging
import uuid as uuid_mod

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, g
from dotenv import load_dotenv

load_dotenv()

from cms.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

# Sentry — opt-in via SENTRY_DSN env var
_sentry_dsn = os.environ.get('SENTRY_DSN')
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
            environment=os.environ.get('FLASK_ENV', 'development'),
            send_default_pii=False,
        )
        logger.info("Sentry initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Sentry: {e}")

from cms.app_helpers import perf_logger, req_logger, log_performance, log_request

app = Flask(__name__)

@app.before_request
def add_request_id():
    request.request_id = request.headers.get('X-Request-ID') or uuid_mod.uuid4().hex[:12]
    g.request_id = request.request_id

# Load security and application configuration based on FLASK_ENV
from cms.config import get_config
app.config.from_object(get_config())

# CORS — allow same-origin by default, configurable via env var
from flask_cors import CORS
_cors_origins = os.environ.get('CORS_ORIGINS', '*')
if _cors_origins != '*':
    _cors_origins = [o.strip() for o in _cors_origins.split(',')]
CORS(app, origins=_cors_origins, supports_credentials=True)

# Server-side sessions (CacheLib filesystem backend)
from flask_session import Session
from cachelib.file import FileSystemCache
_session_dir = os.path.join(os.path.dirname(__file__), 'flask_session')
app.config.setdefault('SESSION_TYPE', 'cachelib')
app.config.setdefault('SESSION_PERMANENT', True)
app.config.setdefault('SESSION_SERIALIZATION_FORMAT', 'json')
app.config['SESSION_CACHELIB'] = FileSystemCache(
    cache_dir=_session_dir, threshold=5000, default_timeout=28800
)
Session(app)

# Deprecated module-level env var constants (kept for backward compat)
HIBP_API_KEY = os.environ.get('HIBP_API_KEY', '')
TWOCHAT_API_KEY = os.environ.get('TWOCHAT_API_KEY', '')
TWOCHAT_WHATSAPP_NUMBER = os.environ.get('TWOCHAT_WHATSAPP_NUMBER', '')
OVERHEID_API_KEY = os.environ.get('OVERHEID_API_KEY', '')
BRAVE_API_KEY = os.environ.get('BRAVE_API_KEY', '')

# =============================================================================
# Case Management System (CMS) Integration
# =============================================================================

# Encryption key: env var → .cms_key file → auto-generate + persist
if not os.environ.get('CMS_ENCRYPTION_KEY'):
    key_file = os.path.join(os.path.dirname(__file__), '.cms_key')
    if os.path.exists(key_file):
        with open(key_file) as f:
            os.environ['CMS_ENCRYPTION_KEY'] = f.read().strip()
        logger.info("Loaded CMS encryption key from .cms_key file")
    else:
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key().decode()
        os.environ['CMS_ENCRYPTION_KEY'] = new_key
        try:
            with open(key_file, 'w') as f:
                f.write(new_key)
            os.chmod(key_file, 0o600)
            logger.warning("Generated new CMS encryption key — saved to .cms_key (chmod 600). "
                           "Set CMS_ENCRYPTION_KEY env var or keep .cms_key for persistence.")
        except Exception as e:
            logger.error(f"Failed to persist encryption key to .cms_key: {e}")

# Set secret key for sessions (required for CMS)
if not app.config.get('SECRET_KEY'):
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Database: PostgreSQL if DATABASE_URL is set, fallback to SQLite
database_url = os.environ.get('DATABASE_URL')
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    CMS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'cms.db'))
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{CMS_DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SQLite cannot use connection pooling — override config defaults
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
    app.config.pop('SQLALCHEMY_ENGINE_OPTIONS', None)

# Initialize CMS using create_cms_module
from cms import create_cms_module, csrf
create_cms_module(app)
logger.info("CMS initialized successfully")

# =============================================================================
# Template Filters — registered on the app (used by CMS templates)
# =============================================================================

@app.template_filter('urlize_target')
def urlize_target_filter(text):
    from urllib.parse import quote
    if not text:
        return ''
    text = str(text)
    url_pattern = re.compile(
        r'(https?:\/\/[^\s<>"\'(){}|\\^`\[\]]+)')
    def make_url(m):
        url = m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener">{url[:80]}</a>'
    return url_pattern.sub(make_url, text)


@app.template_filter('result_link')
def result_link_filter(data, type_name):
    from urllib.parse import quote
    if not data:
        return ''
    if type_name == 'email':
        return f'<a href="/cms/search?q={quote(data)}&type=all" class="result-link">{data}</a>'
    elif type_name in ('ip', 'domain'):
        return f'<a href="/cms/search?q={quote(data)}&type=all" class="result-link">{data}</a>'
    return str(data)


PLATFORM_COLORS = {
    'instagram': '#E4405F', 'facebook': '#1877F2', 'twitter': '#1DA1F2',
    'linkedin': '#0A66C2', 'github': '#333333', 'youtube': '#FF0000',
    'tiktok': '#000000', 'snapchat': '#FFFC00', 'reddit': '#FF4500',
    'pinterest': '#E60023', 'telegram': '#2AABEE', 'whatsapp': '#25D366',
    'signal': '#3A76F0', 'discord': '#5865F2', 'medium': '#000000',
    'tumblr': '#35465C', 'twitch': '#9146FF', 'vimeo': '#1AB7EA',
    'x': '#000000', 'threads': '#000000', 'mastodon': '#6364FF',
    'bluesky': '#0085FF', 'strava': '#FC4C02', 'strava': '#FC4C02',
    'flickr': '#0063DC',
}


@app.template_filter('platform_name')
def platform_name_filter(url):
    if not url:
        return ''
    url = str(url).lower()
    platform_names = {
        'instagram.com': 'Instagram', 'facebook.com': 'Facebook',
        'fb.com': 'Facebook', 'twitter.com': 'Twitter',
        'x.com': 'X (Twitter)', 'linkedin.com': 'LinkedIn',
        'github.com': 'GitHub', 'youtube.com': 'YouTube',
        'youtu.be': 'YouTube', 'tiktok.com': 'TikTok',
        'snapchat.com': 'Snapchat', 'reddit.com': 'Reddit',
        'pinterest.com': 'Pinterest', 'telegram.org': 'Telegram',
        't.me': 'Telegram', 'whatsapp.com': 'WhatsApp',
        'signal.org': 'Signal', 'discord.com': 'Discord',
        'discord.gg': 'Discord', 'medium.com': 'Medium',
        'tumblr.com': 'Tumblr', 'twitch.tv': 'Twitch',
        'vimeo.com': 'Vimeo', 'mastodon.social': 'Mastodon',
        'bsky.app': 'Bluesky', 'threads.net': 'Threads',
        'strava.com': 'Strava', 'flickr.com': 'Flickr',
        'blogspot.com': 'Blogger',
    }
    for domain, name in platform_names.items():
        if domain in url:
            return name
    return url.split('/')[2] if url.startswith('http') else url


@app.template_filter('platform_color')
def platform_color_filter(url):
    if not url:
        return '#666'
    url = str(url).lower()
    for key, color in PLATFORM_COLORS.items():
        if key in url:
            return color
    return '#666'

# =============================================================================
# End CMS Integration
# =============================================================================

# Context Processor — inject version + SF health + help topic into all templates
@app.context_processor
def inject_globals():
    from version import get_version
    ctx = {
        'current_version': get_version(),
    }
    try:
        from cms.setting_cache import cached_setting_get
        sf_health = cached_setting_get('spiderfoot_health', '')
        sf_last_ok = cached_setting_get('spiderfoot_last_ok', '')
        ctx['spiderfoot_health'] = sf_health
        ctx['spiderfoot_last_ok'] = sf_last_ok
    except Exception:
        ctx['spiderfoot_health'] = ''
        ctx['spiderfoot_last_ok'] = ''
    # Derive help topic from request endpoint
    try:
        from flask import request
        if request and request.endpoint:
            ep = request.endpoint
            if ep.startswith('cms.'):
                topic = ep.split('.', 1)[1]
                # Map endpoint names to help topic names
                topic_map = {
                    'dashboard': 'dashboard',
                    'cases_crud': 'cases',
                    'cases_state': 'cases',
                    'cases_subjects': 'cases',
                    'cases_reports': 'cases',
                    'clients_crud': 'clients',
                    'clients_archive': 'clients',
                    'subjects_list': 'subjects',
                    'subjects_crud': 'subjects',
                    'subjects_faces': 'subjects',
                    'subjects_rel': 'subjects',
                    'spiderfoot': 'spiderfoot',
                    'settings': 'settings',
                    'osint_search': 'search',
                    'search': 'search',
                }
                ctx['help_topic'] = topic_map.get(topic, 'general')
            else:
                ctx['help_topic'] = 'general'
        else:
            ctx['help_topic'] = 'general'
    except Exception:
        ctx['help_topic'] = 'general'
    return ctx

# =============================================================================
# App-level Routes — imported from route modules
# =============================================================================

from cms.routes.app_bp import app_routes_bp
app.register_blueprint(app_routes_bp)

from cms.routes.system_app import register_system_routes
register_system_routes(app)

logger.info("App-level routes registered")

# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
