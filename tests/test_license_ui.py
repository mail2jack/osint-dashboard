import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from cms.models import db, User
from cms.services.license import (
    _set_setting,
    cache_license,
    get_license_state,
    trial_blocked,
    trial_tenant_limit,
)


def _make_signed_claims(plan="full", expires="2099-01-01", status="active"):
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_b64 = (
        base64.urlsafe_b64encode(
            priv.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode()
        .rstrip("=")
    )
    claims = {
        "plan": plan,
        "expires_at": expires,
        "install_id": "test-install",
    }
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True)
    sig = base64.urlsafe_b64encode(priv.sign(payload.encode())).decode().rstrip("=")
    _set_setting("license_public_key", pub_b64)
    _set_setting("license_payload", payload)
    _set_setting("license_signature", sig)
    _set_setting("license_status", status)


class TestLicenseService:
    def test_no_license_state(self, app):
        with app.app_context():
            state = get_license_state()
            assert state["present"] is False
            assert state["valid"] is False
            assert state["message"] == "Trial mode (no license installed)"

    def test_trial_defaults(self, app, monkeypatch):
        monkeypatch.setenv("LICENSE_ENFORCEMENT", "")
        assert trial_tenant_limit() == 1
        assert trial_blocked("ai") is True
        assert trial_blocked("spiderfoot") is True
        assert trial_blocked("vessel") is True
        assert trial_blocked("phone") is True
        assert trial_blocked("some_other_feature") is False

    def test_trial_gates_disabled_when_enforcement_off(self, app):
        assert trial_blocked("ai") is False

    def test_cache_license_rejects_bad_signature(self, app):
        with app.app_context():
            assert (
                cache_license(
                    {
                        "payload": '{"plan":"trial","expires_at":"2099-01-01"}',
                        "signature": "bogus",
                        "status": "active",
                    }
                )
                is False
            )
            assert get_license_state()["message"] == "Trial mode (no license installed)"

    def test_invalid_signature_state(self, app):
        with app.app_context():
            _set_setting(
                "license_payload", '{"plan":"trial","expires_at":"2099-01-01"}'
            )
            _set_setting("license_signature", "bogus")
            _set_setting("license_status", "active")
            state = get_license_state()
            assert state["present"] is True
            assert state["valid"] is False
            assert "signature invalid" in state["message"]

    def test_valid_full_license(self, app, monkeypatch):
        monkeypatch.setenv("LICENSE_ENFORCEMENT", "")
        with app.app_context():
            _make_signed_claims(plan="full")
            state = get_license_state()
            assert state["valid"] is True
            assert state["plan"] == "full"
            assert state["message"] == "License valid"
            assert trial_blocked("ai") is False

    def test_revoked_state(self, app):
        with app.app_context():
            _make_signed_claims(plan="full", status="revoked")
            state = get_license_state()
            assert state["valid"] is False
            assert state["revoked"] is True
            assert state["message"] == "License revoked by the license server"

    def test_expired_state(self, app):
        with app.app_context():
            _make_signed_claims(plan="trial", expires="2020-01-01")
            state = get_license_state()
            assert state["valid"] is False
            assert state["message"] == "License expired"


class TestLicenseUI:
    def _fresh_admin_client(self, app):
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            db.session.commit()
        c = app.test_client()
        r = c.post(
            "/auth/login",
            data={"email": "admin@localhost", "password": "Test1234!"},
        )
        assert r.status_code == 302
        if r.headers.get("Location", "") == "/auth/2fa/setup":
            c.get(r.headers["Location"])
            with c.session_transaction() as sess:
                secret = sess.get("_2fa_pending_secret")
            if secret:
                import pyotp

                code = pyotp.TOTP(secret).now()
                c.post("/auth/2fa/setup", data={"code": code})
        return c

    def test_settings_page_renders_license_card(self, app):
        c = self._fresh_admin_client(app)
        r = c.get("/cms/settings?category=general")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "License" in body
        assert "Public key" in body
        assert "Trial tenant limit" in body

    def test_settings_card_full_license_shows_unlimited(self, app):
        with app.app_context():
            _make_signed_claims(plan="full")
        c = self._fresh_admin_client(app)
        r = c.get("/cms/settings?category=general")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Unlimited" in body
        assert "Trial tenant limit" not in body

    def test_banner_shown_when_no_license(self, app, monkeypatch):
        monkeypatch.setenv("LICENSE_ENFORCEMENT", "")
        c = self._fresh_admin_client(app)
        r = c.get("/cms/dashboard")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "License" in body
        assert "Trial mode" in body

    def test_banner_shown_for_invalid_license(self, app, monkeypatch):
        monkeypatch.setenv("LICENSE_ENFORCEMENT", "")
        with app.app_context():
            _set_setting(
                "license_payload", '{"plan":"trial","expires_at":"2099-01-01"}'
            )
            _set_setting("license_signature", "bogus")
            _set_setting("license_status", "active")
        c = self._fresh_admin_client(app)
        r = c.get("/cms/dashboard")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "License" in body
        assert "signature invalid" in body

    def test_banner_hidden_for_valid_full_license(self, app):
        with app.app_context():
            _make_signed_claims(plan="full")
        c = self._fresh_admin_client(app)
        r = c.get("/cms/dashboard")
        assert r.status_code == 200
        assert "License" not in r.get_data(as_text=True)

    def test_tenant_limit_blocked_in_trial(self, app, monkeypatch):
        monkeypatch.setenv("LICENSE_ENFORCEMENT", "")
        c = self._fresh_admin_client(app)
        r = c.post(
            "/cms/api/tenants",
            json={"name": "New Tenant", "slug": "newtenant"},
        )
        assert r.status_code == 403
        assert "trial" in r.get_data(as_text=True).lower()

    def test_tenant_limit_allows_when_enforcement_off(self, app):
        c = self._fresh_admin_client(app)
        r = c.post(
            "/cms/api/tenants",
            json={"name": "New Tenant", "slug": "newtenant2"},
        )
        assert r.status_code == 201
