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

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

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

    # Inject theme_style into all templates
    @app.context_processor
    def inject_theme():
        try:
            from .setting_cache import cached_setting_get

            style = cached_setting_get("theme_style", "classic")
            return {"theme_style": style}
        except Exception:
            from .models import db

            db.session.rollback()
            return {"theme_style": "classic"}

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
            from datetime import timedelta

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

        # Create default admin user if none exists
        if not User.query.filter_by(role="admin").first():
            import secrets
            import string

            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            default_password = "".join(secrets.choice(alphabet) for _ in range(20))
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
                "Default admin user created. Set a password immediately via Settings > Users or the password reset flow."
            )
            app.logger.warning(
                "Default admin created — change password immediately. Username: admin."
            )
        else:
            # Ensure existing admin is super admin + linked to first tenant
            for admin_user in User.query.filter_by(role="admin").all():
                if not admin_user.is_super_admin:
                    admin_user.is_super_admin = True
                if not admin_user.tenant_id:
                    admin_user.tenant_id = first_tenant.id
                db.session.commit()
                if not first_tenant.owner_id:
                    first_tenant.owner_id = admin_user.id
                    db.session.commit()

        # Start Telegram bot in background thread
        try:
            from .telegram_bot import start_bot

            start_bot(app)
        except Exception as e:
            app.logger.error("Failed to start Telegram bot: %s", e)
            import traceback

            app.logger.debug(traceback.format_exc())

    return app


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

        app.config["SQLALCHEMY_DATABASE_URI"] = (
            f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        )

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }


__all__ = ["db", "csrf", "create_cms_module", "init_db", "models", "routes", "auth"]
