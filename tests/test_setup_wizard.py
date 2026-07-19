"""Tests for the first-time setup wizard."""

import pytest
from cms.models import db, User, Setting


class TestSetupWizardAccess:
    """Test access controls for wizard routes."""

    def test_unauthenticated_redirect(self, client):
        resp = client.get("/setup/welcome")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("Location", "")

    def test_welcome_page_renders(self, auth_client):
        resp = auth_client.get("/setup/welcome")
        assert resp.status_code == 200

    def test_completed_wizard_redirects_to_dashboard(self, auth_client, app):
        with app.app_context():
            Setting.set("setup_wizard_complete", "true")
        resp = auth_client.get("/setup/welcome")
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers.get("Location", "")

    def test_completed_wizard_root_redirects_to_finish(self, auth_client, app):
        with app.app_context():
            Setting.set("setup_wizard_complete", "true")
        resp = auth_client.get("/setup/")
        assert resp.status_code == 302
        assert "/setup/finish" in resp.headers.get("Location", "")


class TestSetupWizardPassword:
    """Test the password change step."""

    @pytest.fixture(autouse=True)
    def _advance_to_password(self, auth_client):
        auth_client.get("/setup/welcome")
        auth_client.post("/setup/welcome", data={})

    def test_password_change_success(self, auth_client, app):
        resp = auth_client.post(
            "/setup/password",
            data={
                "current_password": "Test1234!",
                "new_password": "NewAdminPass1!",
                "confirm_password": "NewAdminPass1!",
            },
        )
        assert resp.status_code == 302
        assert resp.headers.get("Location", "").endswith("/setup/api_keys")

        with app.app_context():
            user = User.query.filter_by(username="admin").first()
            assert user.check_password("NewAdminPass1!")

    def test_password_wrong_current(self, auth_client):
        resp = auth_client.post(
            "/setup/password",
            data={
                "current_password": "wrongpass1A!",
                "new_password": "NewAdminPass1!",
                "confirm_password": "NewAdminPass1!",
            },
        )
        assert resp.status_code == 200
        assert b"incorrect" in resp.data.lower()

    def test_password_mismatch(self, auth_client):
        resp = auth_client.post(
            "/setup/password",
            data={
                "current_password": "Test1234!",
                "new_password": "NewAdminPass1!",
                "confirm_password": "Different1!",
            },
        )
        assert resp.status_code == 200
        assert b"do not match" in resp.data.lower()

    def test_password_too_short(self, auth_client):
        resp = auth_client.post(
            "/setup/password",
            data={
                "current_password": "Test1234!",
                "new_password": "short",
                "confirm_password": "short",
            },
        )
        assert resp.status_code == 200
        assert b"at least 8" in resp.data.lower()

    def test_password_empty(self, auth_client):
        resp = auth_client.post(
            "/setup/password",
            data={
                "current_password": "Test1234!",
                "new_password": "",
                "confirm_password": "",
            },
        )
        assert resp.status_code == 200

    def test_password_skip(self, auth_client, app):
        resp = auth_client.post("/setup/password", data={"skip": "1"})
        assert resp.status_code == 302
        assert resp.headers.get("Location", "").endswith("/setup/api_keys")
        # Password should remain unchanged
        with app.app_context():
            user = User.query.filter_by(username="admin").first()
            assert user.check_password("Test1234!")


class TestSetupWizardFullFlow:
    """Test full wizard flow end-to-end."""

    def test_skip_all_steps(self, auth_client, app):
        auth_client.get("/setup/welcome")
        auth_client.post("/setup/welcome", data={})
        auth_client.post("/setup/password", data={"skip": "1"})
        auth_client.post("/setup/api_keys", data={"skip": "1"})
        auth_client.post("/setup/smtp", data={"skip": "1"})
        auth_client.post("/setup/ai", data={"skip": "1"})
        auth_client.post("/setup/telegram", data={"skip": "1"})

        resp = auth_client.get("/setup/finish")
        assert resp.status_code == 200

        with app.app_context():
            assert Setting.get("setup_wizard_complete") == "true"
            assert Setting.get("setup_wizard_step") == "finish"

    def test_fill_all_steps(self, auth_client, app):
        auth_client.get("/setup/welcome")
        auth_client.post("/setup/welcome", data={})

        auth_client.post(
            "/setup/password",
            data={
                "current_password": "Test1234!",
                "new_password": "CustomPass123!",
                "confirm_password": "CustomPass123!",
            },
        )

        auth_client.post(
            "/setup/api_keys",
            data={
                "brave_api_key": "brave-key",
                "overheid_api_key": "overheid-key",
                "hibp_api_key": "hibp-key",
            },
        )

        auth_client.post(
            "/setup/smtp",
            data={
                "smtp_server": "mail.example.com",
                "smtp_port": "465",
                "smtp_username": "smtp-user",
                "smtp_password": "smtp-secret",
                "smtp_from_email": "alerts@example.com",
                "smtp_from_name": "Alert System",
            },
        )

        auth_client.post(
            "/setup/ai",
            data={
                "openrouter_api_key": "or-test-key",
                "ollama_url": "http://ollama:11434",
                "ollama_model": "llama3",
            },
        )

        auth_client.post(
            "/setup/telegram",
            data={
                "telegram_bot_token": "tg-bot-token-123",
                "telegram_enabled": "1",
                "telegram_allowed_users": "@security",
            },
        )

        resp = auth_client.get("/setup/finish")
        assert resp.status_code == 200

        with app.app_context():
            assert Setting.get("setup_wizard_complete") == "true"
            assert Setting.get("brave_api_key") == "brave-key"
            assert Setting.get("overheid_api_key") == "overheid-key"
            assert Setting.get("hibp_api_key") == "hibp-key"
            assert Setting.get("smtp_server") == "mail.example.com"
            assert Setting.get("smtp_port") == "465"
            assert Setting.get("openrouter_api_key") == "or-test-key"
            assert Setting.get("ollama_url") == "http://ollama:11434"
            assert Setting.get("telegram_bot_token") == "tg-bot-token-123"
            assert Setting.get("telegram_enabled") == "true"
            user = User.query.filter_by(username="admin").first()
            assert user.check_password("CustomPass123!")

    def test_wizard_redirects_after_complete(self, auth_client, app):
        with app.app_context():
            Setting.set("setup_wizard_complete", "true")
        # /setup/ root → /setup/finish; all others → dashboard
        for path in [
            "/setup/welcome",
            "/setup/password",
            "/setup/api_keys",
            "/setup/smtp",
            "/setup/ai",
            "/setup/telegram",
        ]:
            resp = auth_client.get(path)
            assert resp.status_code == 302, f"{path} should redirect when wizard done"
            loc = resp.headers.get("Location", "")
            assert "/dashboard" in loc, (
                f"{path} should redirect to dashboard, got {loc}"
            )
        resp = auth_client.get("/setup/")
        assert resp.status_code == 302
        assert "/setup/finish" in resp.headers.get("Location", "")


class TestSetupWizardLoginRedirect:
    """Test that login redirects superadmins to the wizard."""

    def test_login_redirects_to_wizard(self, app, client):
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            db.session.commit()

        resp = client.post(
            "/auth/login",
            data={"email": "admin@localhost", "password": "Test1234!"},
        )
        assert resp.status_code == 302
        loc = resp.headers.get("Location", "")
        assert "setup" in loc, f"Login should redirect to wizard, got {loc}"
