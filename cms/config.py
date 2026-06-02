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

    # CMS Encryption Key (REQUIRED in production)
    CMS_ENCRYPTION_KEY = os.environ.get("CMS_ENCRYPTION_KEY")

    # CMS API Key (for programmatic access)
    CMS_API_KEY = os.environ.get("CMS_API_KEY")

    # File Upload
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/var/uploads/cms")
    ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg"}

    # Pagination
    CMS_ITEMS_PER_PAGE = 20
    CMS_MAX_SEARCH_RESULTS = 100


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
