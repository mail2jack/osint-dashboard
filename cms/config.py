"""
CMS Configuration
==================
Configuration settings for Case Management System.
"""

import os
from datetime import timedelta


class Config:
    """Base configuration."""

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        "connect_args": {"sslmode": os.environ.get("DB_SSL_MODE", "prefer")},
    }

    # Security
    SECRET_KEY = os.environ.get("SECRET_KEY")
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Database SSL mode (disable | allow | prefer | require | verify-ca | verify-full)
    DB_SSL_MODE = os.environ.get("DB_SSL_MODE", "prefer")

    # CMS Encryption Key (REQUIRED in production)
    CMS_ENCRYPTION_KEY = os.environ.get("CMS_ENCRYPTION_KEY")

    # CMS API Key (for programmatic access)
    CMS_API_KEY = os.environ.get("CMS_API_KEY")

    # File Upload
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/var/uploads/cms")
    ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg"}

    # Stripe
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    # Pagination
    CMS_ITEMS_PER_PAGE = 20
    CMS_MAX_SEARCH_RESULTS = 100

    @classmethod
    def init_app(cls, app):
        """Hook for environment-specific startup validation. No-op by default."""


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    TESTING = False
    SQLALCHEMY_ECHO = False

    # Less strict security for development
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    TESTING = False

    # Strict security
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    WTF_CSRF_ENABLED = True

    DB_SSL_MODE = os.environ.get("DB_SSL_MODE", "require")

    # Engine must also enforce the SSL mode above (base Config reads env at import)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        "connect_args": {"sslmode": os.environ.get("DB_SSL_MODE", "require")},
    }

    # Encryption key is REQUIRED in production
    @classmethod
    def init_app(cls, app):
        Config.init_app(app)

        if not app.config.get("CMS_ENCRYPTION_KEY"):
            raise ValueError(
                "CMS_ENCRYPTION_KEY environment variable is REQUIRED in production. "
                'Generate with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        if not app.config.get("SECRET_KEY"):
            raise ValueError(
                "SECRET_KEY environment variable is REQUIRED in production. "
                'Use: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise ValueError(
                "DATABASE_URL environment variable is REQUIRED in production."
            )
        uri = str(app.config.get("SQLALCHEMY_DATABASE_URI"))
        if not uri.startswith("postgresql"):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL in production. "
                "SQLite disables multi-tenant RLS isolation."
            )


class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


# Configuration mapping
config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config(env: str = None) -> Config:
    """Get configuration by environment name."""
    if env is None:
        env = os.environ.get("FLASK_ENV", "development")
    return config_by_name.get(env, DevelopmentConfig)
