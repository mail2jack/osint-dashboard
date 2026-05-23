import os
import tempfile
import atexit
import pytest

_db_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
atexit.register(lambda: os.unlink(_db_file.name) if os.path.exists(_db_file.name) else None)
os.environ['DATABASE_URL'] = f'sqlite:///{_db_file.name}'
os.environ['FLASK_SECRET_KEY'] = 'test-secret-key'
os.environ['CMS_ENCRYPTION_KEY'] = 'dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1jaGFycy0tLS0t'

from app import app as _app
from cms.models import db, User, Setting, init_default_settings
from flask_login import login_user


@pytest.fixture
def app():
    _app.config['TESTING'] = True
    _app.config['WTF_CSRF_ENABLED'] = False
    _app.config['SERVER_NAME'] = 'localhost'

    with _app.app_context():
        db.create_all()
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
        db.drop_all()


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
        user_id = user.id  # capture ID while session is active

    # Use session_transaction to set Flask-Login's session keys directly
    from uuid import UUID
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
        sess['_remember'] = 'set'
    return client


@pytest.fixture
def db_session(app):
    return db.session
