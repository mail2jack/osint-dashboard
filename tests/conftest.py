import os
import tempfile
import atexit
import pytest

_db_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
atexit.register(lambda: os.unlink(_db_file.name) if os.path.exists(_db_file.name) else None)
os.environ['DATABASE_URL'] = f'sqlite:///{_db_file.name}'
os.environ['FLASK_SECRET_KEY'] = 'test-secret-key'
os.environ['CMS_ENCRYPTION_KEY'] = 'ZFnorYZ7TTJCbUd8J-NCId5SkbzkB450u8odzL65yj8='

# Import the real Flask app — create_cms_module runs at import time
from app import app as _app
from cms.models import db, User, Setting, init_default_settings
from flask_login import login_user
from sqlalchemy import inspect, text


@pytest.fixture
def app():
    _app.config['TESTING'] = True
    _app.config['WTF_CSRF_ENABLED'] = False
    _app.config['SERVER_NAME'] = 'localhost'

    with _app.app_context():
        # Recreate schema via Alembic (caters for teardown dropping all tables)
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), '..', 'alembic.ini'))
        command.upgrade(alembic_cfg, 'head')
        init_default_settings()

        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@test.nl',
                full_name='Admin User',
                role='admin',
                is_active=True,
            )
            admin.set_password('test1234')
            db.session.add(admin)
            db.session.commit()

        yield _app

        db.session.remove()
        # Drop ALL tables including alembic_version (not in SQLAlchemy metadata)
        for table in inspect(db.engine).get_table_names():
            db.session.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(app, client):
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        import pyotp
        user.totp_secret = pyotp.random_base32()
        user.totp_enabled = True
        db.session.commit()
        user_id = user.id

    from uuid import UUID
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
        sess['_remember'] = 'set'
    return client


@pytest.fixture
def db_session(app):
    return db.session
