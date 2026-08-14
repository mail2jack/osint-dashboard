"""
Case Management System (CMS) Blueprint
=======================================
Professional investigation management for legal and forensic applications.

This module integrates with the existing Flask OSINT application,
providing:
- Role-Based Access Control (RBAC)
- Field-Level Encryption for GDPR compliance
- Comprehensive Audit Logging
- Case and Client Management
- Subject and Financial Tracking
"""

import os
from datetime import timedelta

from flask import Flask
from flask_migrate import Migrate
from flask_wtf import CSRFProtect

from .models import db, User


migrate = Migrate()
csrf = CSRFProtect()


def create_cms_module(app: Flask):
    """
    Initialize CMS module with Flask application.

    Args:
        app: Flask application instance
    """
    # Initialize extensions
    from .auth import login_manager
    from .background import init_background

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    init_background(app)

    # Configure login
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access the Case Management System."
    login_manager.login_message_category = "info"

    # Register blueprints
    from .routes import cms_bp
    from .routes import register_modules

    register_modules()
    from .auth import auth_bp, users_bp

    app.register_blueprint(cms_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)

    from .api_v1 import api_v1_bp

    app.register_blueprint(api_v1_bp)

    from .routes.setup_wizard import setup_wizard_bp

    app.register_blueprint(setup_wizard_bp)

    # Inject theme_style into all templates
    @app.context_processor
    def inject_theme():
        try:
            from .setting_cache import cached_setting_get

            style = cached_setting_get("theme_style", "classic")
            logo = cached_setting_get("app_logo", "")
            try:
                from flask_login import current_user

                if current_user.is_authenticated and current_user.tenant_id:
                    from .models import TenantSetting

                    tlogo = TenantSetting.get(
                        "app_logo", tenant_id=current_user.tenant_id
                    )
                    if tlogo:
                        logo = "tenant_logos/" + tlogo
            except Exception:
                pass
            return {"theme_style": style, "app_logo": logo}
        except Exception:
            from .models import db

            db.session.rollback()
            return {"theme_style": "classic", "app_logo": ""}

    # Inject license banner state into all templates (only when license is not
    # fully active, e.g. trial / expired / revoked / invalid).
    @app.context_processor
    def inject_license_state():
        try:
            from flask_login import current_user

            if not current_user.is_authenticated:
                return {"license_state": None}
        except Exception:
            return {"license_state": None}
        try:
            from .services import license as license_service

            if license_service.enforcement_off():
                return {"license_state": None}
            state = license_service.get_license_state()
            if state.get("valid") and (state.get("plan") or "").lower() in (
                "full",
                "professional",
                "enterprise",
            ):
                return {"license_state": None}
            return {"license_state": state}
        except Exception:
            from .models import db

            db.session.rollback()
            return {"license_state": None}

    # Schema management via Alembic
    with app.app_context():
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config(
            os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
        )

        from sqlalchemy import inspect

        inspector = inspect(db.engine)
        has_alembic = "alembic_version" in inspector.get_table_names()
        has_app_tables = bool(
            [t for t in inspector.get_table_names() if t not in ("alembic_version",)]
        )

        if has_alembic:
            # Normal incremental migration path
            command.upgrade(alembic_cfg, "head")
        elif has_app_tables:
            # Existing DB (pre-Alembic) — stamp head without running migrations
            app.logger.info("Existing DB detected — stamping Alembic head")
            command.stamp(alembic_cfg, "head")
        else:
            # Fresh DB — create all tables from migration
            command.upgrade(alembic_cfg, "head")

            # All schema migrations are now managed by Alembic.
        # See migrations/versions/ for the current schema revision.

        # Startup data migrations run outside a request, so establish a trusted
        # context before querying tenant-protected tables on PostgreSQL.
        if db.engine.dialect.name == "postgresql":
            from sqlalchemy import text
            from .tenant_context import set_tenant_context

            startup_tenant = db.session.execute(
                text("SELECT id FROM tenants ORDER BY id LIMIT 1")
            ).scalar()
            set_tenant_context(db, startup_tenant, bypass_rls=True)

        # Data migration: Subject.notes free-text → Comment model
        try:
            from .models import Subject, Comment
            from datetime import datetime, timezone

            subjects_with_notes = Subject.query.filter(
                Subject.notes.isnot(None), Subject.notes != ""
            ).all()
            migrated_count = 0
            for s in subjects_with_notes:
                existing = Comment.query.filter_by(
                    subject_id=s.id, content=s.notes, comment_type="note"
                ).first()
                if not existing:
                    comment = Comment(
                        subject_id=s.id,
                        content=s.notes,
                        comment_type="note",
                        author_id=User.query.filter_by(role="admin").first().id,
                        created_at=s.created_at or datetime.now(timezone.utc),
                        updated_at=s.updated_at or datetime.now(timezone.utc),
                    )
                    db.session.add(comment)
                    migrated_count += 1
            if migrated_count:
                db.session.commit()
                app.logger.info(
                    f"Migration: migrated {migrated_count} Subject.notes → Comment"
                )
        except Exception as e:
            app.logger.warning(f"Subject.notes migration note: {e}")
            db.session.rollback()

        # Initialize default settings
        from .models import Setting, AuditLog, init_default_settings

        init_default_settings()

        # Apply session timeout from settings, fallback to 8h (28800s)
        timeout_seconds = 28800
        try:
            timeout_minutes = Setting.get("session_timeout_minutes", "480")
            if timeout_minutes is not None:
                timeout_seconds = int(timeout_minutes) * 60
        except Exception as e:
            app.logger.debug(f"Session timeout fallback: {e}")
        app.permanent_session_lifetime = timedelta(seconds=timeout_seconds)
        app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=timeout_seconds)

        # Seed default service rates for auto-invoicing
        try:
            from .services.invoice_service import seed_service_rates

            seed_service_rates()
        except Exception as e:
            app.logger.debug(f"Service rate seed note: {e}")
            db.session.rollback()

        # Purge old audit logs on startup
        try:
            retention = int(Setting.get("audit_log_retention_days", "365"))
            if retention > 0:
                deleted = AuditLog.purge_old(retention)
                if deleted:
                    app.logger.info(
                        f"Startup: purged {deleted} audit log entries older than {retention} days"
                    )
        except Exception as e:
            app.logger.debug(f"Audit log purge note: {e}")
            db.session.rollback()

        # Purge expired password reset tokens on startup
        try:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            expired = User.query.filter(
                User.password_reset_expires.isnot(None),
                User.password_reset_expires < now,
            ).update(
                {"password_reset_token": None, "password_reset_expires": None},
                synchronize_session="fetch",
            )
            db.session.commit()
            if expired:
                app.logger.info(
                    f"Startup: cleared {expired} expired password reset tokens"
                )
        except Exception as e:
            app.logger.debug(f"Password reset token cleanup note: {e}")
            db.session.rollback()

        # Clean up old background tasks on startup
        try:
            from .background import cleanup_old_tasks

            cleanup_old_tasks(max_age_hours=48)
        except Exception as e:
            app.logger.debug(f"Background task cleanup note: {e}")
            db.session.rollback()

        # Purge old phone lookups on startup
        try:
            from .models import PhoneLookup

            retention = int(Setting.get("phone_lookup_retention_days", "90"))
            if retention > 0:
                deleted = PhoneLookup.purge_old(retention)
                if deleted:
                    app.logger.info(
                        f"Startup: purged {deleted} phone lookup entries older than {retention} days"
                    )
        except Exception as e:
            app.logger.debug(f"Phone lookup purge note: {e}")
            db.session.rollback()

        # Purge old login logs on startup
        try:
            from .models import LoginLog

            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            deleted = LoginLog.query.filter(LoginLog.created_at < cutoff).delete()
            if deleted:
                db.session.commit()
                app.logger.info(
                    f"Startup: purged {deleted} login log entries older than 365 days"
                )
        except Exception as e:
            app.logger.debug(f"Login log purge note: {e}")
            db.session.rollback()

        # Migrate API keys from .env to Setting table (if not already set)
        try:
            _env_settings = {
                "overheid_api_key": "OVERHEID_API_KEY",
                "brave_api_key": "BRAVE_API_KEY",
                "twochat_api_key": "TWOCHAT_API_KEY",
                "hibp_api_key": "HIBP_API_KEY",
                "twochat_whatsapp_number": "TWOCHAT_WHATSAPP_NUMBER",
            }
            for setting_name, env_var in _env_settings.items():
                existing = Setting.get(setting_name, "")
                if not existing:
                    val = os.environ.get(env_var, "")
                    if val:
                        Setting.set(setting_name, val)
                        app.logger.info(
                            "Migrated %s from .env to Setting table", setting_name
                        )
            db.session.commit()
        except Exception as e:
            app.logger.debug("API key migration note: %s", e)
            db.session.rollback()

        # Seed first tenant if none exists
        from .models import Tenant

        first_tenant = Tenant.query.first()
        if not first_tenant:
            import uuid as _uuid

            first_tenant = Tenant(
                id=str(_uuid.uuid4()),
                name="Default Organization",
                slug="default",
                is_active=True,
                tier="enterprise",
            )
            db.session.add(first_tenant)
            db.session.commit()
            app.logger.info(
                "Seeded default tenant: %s (%s)", first_tenant.name, first_tenant.id
            )
        elif first_tenant.tier == "free":
            first_tenant.tier = "enterprise"
            db.session.commit()
            app.logger.info("Upgraded default tenant tier: free → enterprise")

        # Create default admin user if none exists
        if not User.query.filter_by(role="admin").first():
            # Default password matches INSTALL.md / MANUAL.md / setup wizard.
            # The setup wizard forces a password change on first login.
            default_password = "changeme123"
            admin = User(
                username="admin",
                email="admin@localhost",
                full_name="System Administrator",
                role="admin",
                tenant_id=first_tenant.id,
                is_super_admin=True,
            )
            admin.set_password(default_password)
            db.session.add(admin)
            db.session.commit()
            first_tenant.owner_id = admin.id
            db.session.commit()
            app.logger.warning(
                "Default admin user created with password 'changeme123'. "
                "The setup wizard will force a password change on first login."
            )
        else:
            # Ensure existing admin of the default tenant is super admin + linked
            for admin_user in User.query.filter_by(
                role="admin", tenant_id=first_tenant.id
            ).all():
                if not admin_user.is_super_admin:
                    admin_user.is_super_admin = True
                if not admin_user.tenant_id:
                    admin_user.tenant_id = first_tenant.id
                db.session.commit()
                if not first_tenant.owner_id:
                    first_tenant.owner_id = admin_user.id
                    db.session.commit()

        # Seed existing non-admin users without tenant_id → default tenant
        orphan_users = User.query.filter(
            User.tenant_id.is_(None), User.role != "admin"
        ).all()
        if orphan_users:
            linked = 0
            for u in orphan_users:
                u.tenant_id = first_tenant.id
                linked += 1
            if linked:
                db.session.commit()
                app.logger.info(
                    "Seed: linked %d existing non-admin users to default tenant",
                    linked,
                )

    init_telemetry(app)
    return app


def init_telemetry(app: Flask) -> None:
    try:
        from .services.telemetry import init_telemetry as _init_telemetry

        _init_telemetry(app)
    except Exception as e:
        app.logger.debug("Telemetry init skipped: %s", e)


def init_db(app: Flask, database_url: str = None):
    """
    Configure database for CMS.

    Args:
        app: Flask application instance
        database_url: PostgreSQL connection string
    """
    if database_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    elif not app.config.get("SQLALCHEMY_DATABASE_URI"):
        # Default to PostgreSQL only if not already set
        db_host = app.config.get("DB_HOST", "localhost")
        db_port = app.config.get("DB_PORT", "5432")
        db_name = app.config.get("DB_NAME", "cms_db")
        db_user = app.config.get("DB_USER", "postgres")
        db_pass = app.config.get("DB_PASSWORD", "")

        ssl_mode = app.config.get("DB_SSL_MODE", "prefer")
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}?sslmode={ssl_mode}"
        )

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    ssl_mode = app.config.get("DB_SSL_MODE", "prefer")
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        "connect_args": {"sslmode": ssl_mode},
    }


__all__ = ["db", "csrf", "create_cms_module", "init_db", "models", "routes", "auth"]
