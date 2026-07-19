import os
import secrets
import base64
import tempfile
import atexit
import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False, delete_on_close=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
os.environ["FLASK_SECRET_KEY"] = secrets.token_hex(32)
os.environ["CMS_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
    secrets.token_bytes(32)
).decode()

from app import app as _app
from cms.models import db, User, init_default_settings
from sqlalchemy import inspect, text

atexit.register(
    lambda: os.unlink(_tmp_db.name) if os.path.exists(_tmp_db.name) else None
)


@pytest.fixture(scope="session")
def app():
    _app.config["TESTING"] = True
    _app.config["WTF_CSRF_ENABLED"] = False
    _app.config["SERVER_NAME"] = "localhost"

    with _app.app_context():
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config(
            os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
        )
        command.upgrade(alembic_cfg, "head")
        init_default_settings()
        from cms.services.invoice_service import seed_service_rates

        seed_service_rates()

        admin = User.query.filter_by(username="admin").first()
        if admin:
            admin.set_password("Test1234!")
        else:
            admin = User(
                username="admin",
                email="admin@localhost",
                full_name="Admin User",
                role="admin",
                is_active=True,
                is_super_admin=True,
            )
            admin.set_password("Test1234!")
            db.session.add(admin)
        db.session.commit()

        # Ensure existing admin has super_admin flag (matches production init_default_settings)
        admin = User.query.filter_by(role="admin").first()
        if admin and not admin.is_super_admin:
            admin.is_super_admin = True
            db.session.commit()

        yield _app

        db.session.remove()
        for table in inspect(db.engine).get_table_names():
            db.session.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        db.session.commit()


# Prepending a before_request handler to clear stale g._login_user from previous
# tests. Must run BEFORE set_tenant_context (registered in app.py). Flask 3.x
# scopes `g` to the app context, so the session-scoped app fixture keeps
# g._login_user alive between tests. This causes _get_user() to skip _load_user
# entirely (the "_login_user" not in g check) and never re-read the session.
from flask import g as _g


def _clear_g_login_user():
    if _app.testing:
        _g.pop("_login_user", None)


_app.before_request_funcs.setdefault(None, []).insert(0, _clear_g_login_user)


@pytest.fixture(autouse=True)
def _clean_db_between_tests(app):
    """Clean all data between tests — runs before each test function."""
    db.session.rollback()  # Clear any aborted transaction from previous test

    # Delete all tables EXCEPT seed/config tables (keep admin + tenant from app fixture)
    for t in inspect(db.engine).get_table_names():
        if t in ("alembic_version", "users", "tenants", "service_rates"):
            continue
        db.session.execute(text(f'DELETE FROM "{t}"'))
    db.session.commit()

    # Re-set g.tenant_id so _fill_tenant_id can auto-populate tenant_id on ORM
    # inserts made directly in test bodies (not via HTTP requests).
    # Flask 3.x scopes g to the app context, so this persists across tests.
    from flask import g as _g

    admin = User.query.filter_by(role="admin").first()
    if admin:
        _g.tenant_id = admin.tenant_id


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(app, client):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()

        if user is None:
            user = User(
                username="admin",
                email="admin@localhost",
                full_name="Admin User",
                role="admin",
                is_active=True,
            )
            user.set_password("Test1234!")
            db.session.add(user)
            db.session.commit()
        else:
            user.set_password("Test1234!")
            db.session.commit()

        user.totp_secret = None
        user.totp_enabled = False
        db.session.commit()
        user_id = str(user.id)

    with client.session_transaction() as sess:
        sess["_user_id"] = user_id
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


@pytest.fixture
def db_session():
    from flask import g

    if "tenant_id" not in g:
        admin = User.query.filter_by(username="admin").first()
        g.tenant_id = admin.tenant_id if admin else None
    return db.session
