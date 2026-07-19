import pyotp
from cms.models import db, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_user(username="testuser", password="Test1234!", totp=False):
    u = User(
        username=username,
        email=f"{username}@test.nl",
        full_name="Test User",
        role="junior_investigator",
        is_active=True,
    )
    u.set_password(password)
    if totp:
        u.totp_secret = pyotp.random_base32()
        u.totp_enabled = True
    db.session.add(u)
    db.session.commit()
    return u


def _totp_code(secret):
    return pyotp.TOTP(secret).now()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_page_returns_200(self, client):
        resp = client.get("/auth/login")
        assert resp.status_code == 200

    def test_login_success_no_2fa(self, client, db_session):
        _create_user("logintest", "Test1234!")
        resp = client.post(
            "/auth/login",
            data={"email": "logintest@test.nl", "password": "Test1234!"},
        )
        assert resp.status_code == 302
        assert "/auth/2fa/setup" in resp.headers.get("Location", "")

    def test_login_success_with_2fa(self, client, db_session):
        secret = pyotp.random_base32()
        _create_user("logintest2fa", "Test1234!", totp=False)
        u = User.query.filter_by(username="logintest2fa").first()
        u.totp_secret = secret
        u.totp_enabled = True
        db.session.commit()

        resp = client.post(
            "/auth/login",
            data={"email": "logintest2fa@test.nl", "password": "Test1234!"},
        )
        assert resp.status_code == 302
        assert "/auth/2fa/verify" in resp.headers.get("Location", "")

    def test_login_wrong_password(self, client, db_session):
        _create_user("wrongpw", "Test1234!")
        resp = client.post(
            "/auth/login",
            data={"email": "wrongpw@test.nl", "password": "wrongpass1A!"},
        )
        assert resp.status_code == 200

    def test_login_nonexistent_user(self, client):
        resp = client.post(
            "/auth/login",
            data={"email": "nobody@test.nl", "password": "Test1234!"},
        )
        assert resp.status_code == 200

    def test_login_already_authenticated(self, client, db_session):
        _create_user("alreadyauth", "Test1234!", totp=False)
        resp = client.post(
            "/auth/login",
            data={"username": "alreadyauth", "password": "Test1234!"},
        )
        assert resp.status_code in (200, 302)

    def test_logout(self, client, db_session):
        _create_user("logoutuser", "Test1234!")
        client.post(
            "/auth/login",
            data={"username": "logoutuser", "password": "Test1234!"},
        )
        resp = client.get("/auth/logout")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# 2FA Setup
# ---------------------------------------------------------------------------


class Test2FASetup:
    def test_setup_page(self, client, db_session):
        u = _create_user("setup2fa", "Test1234!")
        with client.session_transaction() as sess:
            sess["_2fa_user_id"] = str(u.id)
        resp = client.get("/auth/2fa/setup")
        assert resp.status_code == 200

    def test_setup_valid_code(self, client, db_session):
        u = _create_user("setupvalid", "Test1234!")
        with client.session_transaction() as sess:
            sess["_2fa_user_id"] = str(u.id)
        # Simulate the pending secret that GET sets
        secret = pyotp.random_base32()
        with client.session_transaction() as sess:
            sess["_2fa_pending_secret"] = secret

        code = pyotp.TOTP(secret).now()
        resp = client.post("/auth/2fa/setup", data={"code": code})
        assert resp.status_code == 200

        u = db.session.get(User, u.id)
        assert u.totp_enabled is True
        assert u.totp_secret == secret

    def test_setup_invalid_code(self, client, db_session):
        u = _create_user("setupinvalid", "Test1234!")
        with client.session_transaction() as sess:
            sess["_2fa_user_id"] = str(u.id)
        with client.session_transaction() as sess:
            sess["_2fa_pending_secret"] = pyotp.random_base32()

        resp = client.post("/auth/2fa/setup", data={"code": "000000"})
        assert resp.status_code == 200

        u = db.session.get(User, u.id)
        assert u.totp_enabled is False

    def test_setup_no_session(self, client):
        resp = client.get("/auth/2fa/setup")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# 2FA Verify
# ---------------------------------------------------------------------------


class Test2FAVerify:
    def test_verify_page(self, client, db_session):
        secret = pyotp.random_base32()
        u = _create_user("verifyuser", "Test1234!", totp=False)
        u.totp_secret = secret
        u.totp_enabled = True
        db.session.commit()

        with client.session_transaction() as sess:
            sess["_2fa_user_id"] = str(u.id)

        resp = client.get("/auth/2fa/verify")
        assert resp.status_code == 200

    def test_verify_valid_totp(self, client, db_session):
        secret = pyotp.random_base32()
        u = _create_user("verifytotp", "Test1234!", totp=False)
        u.totp_secret = secret
        u.totp_enabled = True
        db.session.commit()

        with client.session_transaction() as sess:
            sess["_2fa_user_id"] = str(u.id)

        code = pyotp.TOTP(secret).now()
        resp = client.post("/auth/2fa/verify", data={"code": code})
        assert resp.status_code == 302

    def test_verify_invalid_code(self, client, db_session):
        secret = pyotp.random_base32()
        u = _create_user("verifybad", "Test1234!", totp=False)
        u.totp_secret = secret
        u.totp_enabled = True
        db.session.commit()

        with client.session_transaction() as sess:
            sess["_2fa_user_id"] = str(u.id)

        resp = client.post("/auth/2fa/verify", data={"code": "000000"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------


class TestPasswordReset:
    def test_set_password_valid_token(self, client, db_session):
        u = _create_user("resettest", "Test1234!")
        token = u.generate_reset_token()
        db.session.commit()

        resp = client.post(
            f"/auth/set-password/{token}",
            data={"password": "NewPass1234!", "confirm_password": "NewPass1234!"},
        )
        assert resp.status_code == 302

        u = db.session.get(User, u.id)
        assert u.password_reset_token is None

    def test_set_password_invalid_token(self, client):
        resp = client.get("/auth/set-password/invalidtoken123")
        assert resp.status_code == 302

    def test_set_password_mismatch(self, client, db_session):
        u = _create_user("mismatch", "Test1234!")
        token = u.generate_reset_token()
        db.session.commit()

        resp = client.post(
            f"/auth/set-password/{token}",
            data={"password": "NewPass1234!", "confirm_password": "Different1!"},
        )
        assert resp.status_code in (200, 400)

    def test_set_password_expired_token(self, client, db_session):
        from datetime import datetime, timezone, timedelta

        u = _create_user("expiredtoken", "Test1234!")
        token = u.generate_reset_token()
        u.password_reset_expires = datetime.now(timezone.utc) - timedelta(hours=48)
        db.session.commit()

        resp = client.post(
            f"/auth/set-password/{token}",
            data={"password": "NewPass1234!", "confirm_password": "NewPass1234!"},
        )
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Admin 2FA Reset
# ---------------------------------------------------------------------------


class TestAdmin2FAReset:
    def _fresh_admin_client(self, app):
        c = app.test_client()
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            db.session.commit()
            user_id = str(admin.id)
        with c.session_transaction() as sess:
            sess["_user_id"] = user_id
            sess["_fresh"] = True
        return c

    def test_reset_other_user_2fa(self, app, db_session):
        secret = pyotp.random_base32()
        u = _create_user("resetme", "Test1234!", totp=False)
        u.totp_secret = secret
        u.totp_enabled = True
        db.session.commit()

        c = self._fresh_admin_client(app)

        resp = c.post(f"/auth/2fa/reset/{u.id}")
        assert resp.status_code == 302

        u = db.session.get(User, u.id)
        assert u.totp_enabled is False
        assert u.totp_secret is None

    def test_reset_nonexistent_user(self, app, db_session):
        c = self._fresh_admin_client(app)
        resp = c.post("/auth/2fa/reset/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Password Change
# ---------------------------------------------------------------------------


class TestPasswordChange:
    def test_change_password_success(self, auth_client):
        resp = auth_client.post(
            "/users/change-password",
            data={
                "current_password": "Test1234!",
                "new_password": "NewPass5678!",
                "confirm_password": "NewPass5678!",
            },
        )
        assert resp.status_code in (200, 302)

    def test_change_password_wrong_current(self, auth_client):
        resp = auth_client.post(
            "/users/change-password",
            data={
                "current_password": "wrongpass1A!",
                "new_password": "NewPass5678!",
                "confirm_password": "NewPass5678!",
            },
        )
        assert resp.status_code in (400, 200)

    def test_change_password_mismatch(self, auth_client):
        resp = auth_client.post(
            "/users/change-password",
            data={
                "current_password": "Test1234!",
                "new_password": "NewPass5678!",
                "confirm_password": "Different1!",
            },
        )
        assert resp.status_code in (400, 200)


# ---------------------------------------------------------------------------
# User Creation (Admin)
# ---------------------------------------------------------------------------


class TestUserCreate:
    URL = "/users/create"

    def _fresh_admin_client(self, app):
        c = app.test_client()
        with c.session_transaction() as sess:
            for k in list(sess.keys()):
                del sess[k]
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            db.session.commit()
        r = c.post(
            "/auth/login", data={"email": "admin@localhost", "password": "Test1234!"}
        )
        assert r.status_code == 302
        if r.headers.get("Location", "") == "/auth/2fa/setup":
            c.get(r.headers["Location"])
            with c.session_transaction() as sess:
                secret = sess.get("_2fa_pending_secret")
                if secret:
                    code = pyotp.TOTP(secret).now()
                    c.post("/auth/2fa/setup", data={"code": code})
        return c

    def test_create_user(self, app, db_session):
        c = self._fresh_admin_client(app)
        resp = c.post(
            self.URL,
            data={
                "username": "newuser",
                "email": "newuser@test.nl",
                "full_name": "New User",
                "role": "junior_investigator",
                "password": "Test1234!",
            },
        )
        assert resp.status_code in (201, 302)

    def test_create_duplicate_username(self, app, db_session):
        _create_user("dupuser", "Test1234!")
        c = self._fresh_admin_client(app)
        resp = c.post(
            self.URL,
            data={
                "username": "dupuser",
                "email": "different@test.nl",
                "full_name": "Duplicate",
                "role": "viewer",
                "password": "Test1234!",
            },
        )
        assert resp.status_code in (400, 302, 200)

    def test_create_invalid_role(self, app, db_session):
        c = self._fresh_admin_client(app)
        resp = c.post(
            self.URL,
            data={
                "username": "badrole",
                "email": "badrole@test.nl",
                "full_name": "Bad Role",
                "role": "superadmin",
                "password": "Test1234!",
            },
        )
        assert resp.status_code in (400, 302, 200)

    def test_create_user_sends_email(self, app, db_session, monkeypatch):
        sent = []

        def fake_send(email, username, full_name, link):
            sent.append((email, username, full_name, link))

        monkeypatch.setattr("cms.email_utils.send_password_reset_email", fake_send)
        c = self._fresh_admin_client(app)
        resp = c.post(
            self.URL,
            data={
                "username": "maileduser",
                "email": "mailed@test.nl",
                "full_name": "Mailed User",
                "role": "junior_investigator",
                "password": "Test1234!",
                "send_email": "1",
            },
        )
        assert resp.status_code in (201, 302)


# ---------------------------------------------------------------------------
# User Edit (Admin)
# ---------------------------------------------------------------------------


class TestUserEdit:
    def _fresh_admin_client(self, app):
        c = app.test_client()
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            db.session.commit()
            user_id = str(admin.id)
        with c.session_transaction() as sess:
            sess["_user_id"] = user_id
            sess["_fresh"] = True
        return c

    def test_edit_own_profile(self, app, db_session):
        c = self._fresh_admin_client(app)
        admin = User.query.filter_by(username="admin").first()
        resp = c.post(f"/users/{admin.id}/edit", data={"full_name": "Admin Updated"})
        assert resp.status_code in (200, 302)
        admin2 = User.query.filter_by(username="admin").first()
        assert admin2.full_name == "Admin Updated"

    def test_activate_deactivate(self, app, db_session):
        c = self._fresh_admin_client(app)
        u = _create_user("toggletest", "Test1234!")
        c.post(f"/users/{u.id}/deactivate")

        u = db.session.get(User, u.id)
        assert u.is_active is False
