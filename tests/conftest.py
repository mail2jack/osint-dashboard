import os
import tempfile
import atexit
import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False, delete_on_close=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"
os.environ["CMS_ENCRYPTION_KEY"] = "ZFnorYZ7TTJCbUd8J-NCId5SkbzkB450u8odzL65yj8="

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

        admin = User.query.filter_by(username="admin").first()
        if admin:
            admin.set_password("Test1234!")
        else:
            admin = User(
                username="admin",
                email="admin@test.nl",
                full_name="Admin User",
                role="admin",
                is_active=True,
            )
            admin.set_password("Test1234!")
            db.session.add(admin)
        db.session.commit()

        yield _app

        db.session.remove()
        for table in inspect(db.engine).get_table_names():
            db.session.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        db.session.commit()


@pytest.fixture(autouse=True)
def _clean_db_between_tests(app):
    """Clean all data between tests — runs before each test function."""
    # Delete all tables EXCEPT users (keep the admin from app fixture)
    for t in inspect(db.engine).get_table_names():
        if t in ("alembic_version", "users"):
            continue
        db.session.execute(text(f'DELETE FROM "{t}"'))
    db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(app, client):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        import pyotp

        if user is None:
            user = User(
                username="admin",
                email="admin@test.nl",
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

        user.totp_secret = pyotp.random_base32()
        user.totp_enabled = True
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


@pytest.fixture
def db_session():
    return db.session
