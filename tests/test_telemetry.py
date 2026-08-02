"""Tests for the telemetry client (cms/services/telemetry.py)."""

import pytest

from cms.models import Setting, init_default_settings
from cms.services import telemetry


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400


class FakePost:
    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def __call__(self, url, payload, **kwargs):
        self.calls.append((url, payload, kwargs))
        return FakeResponse(self.status)


@pytest.fixture(autouse=True)
def _isolate_identity(monkeypatch):
    monkeypatch.delenv("INSTALL_ID", raising=False)
    monkeypatch.delenv("INSTALL_TOKEN", raising=False)
    monkeypatch.setattr(telemetry, "_append_to_env", lambda *a, **k: None)


def test_telemetry_defaults_seeded(app):
    with app.app_context():
        init_default_settings()
        row = Setting.query.filter_by(key="telemetry_enabled").first()
        assert row is not None
        assert row.is_active is True
        assert row.category == "general"
        assert row.value == "true"
        url = Setting.query.filter_by(key="telemetry_server_url").first()
        assert url is not None
        assert url.value == "https://license.iveras.com"


def test_collect_system_info_shape(app):
    with app.app_context():
        info = telemetry.collect_system_info()
        for key in (
            "hostname",
            "os_name",
            "os_version",
            "kernel",
            "platform",
            "app_version",
            "cpu_model",
            "cpu_count",
            "ram_gb",
            "disk_gb",
            "local_ips",
        ):
            assert key in info, f"missing {key}"
        assert info["hostname"]
        assert info["platform"] in ("docker", "bare-metal")
        assert isinstance(info["local_ips"], list)


def test_telemetry_enabled_default_true(app):
    with app.app_context():
        init_default_settings()
        assert telemetry.is_telemetry_enabled() is True


def test_telemetry_disabled_setting(app):
    with app.app_context():
        Setting.set("telemetry_enabled", "false", category="general")
        assert telemetry.is_telemetry_enabled() is False


def test_register_flow_sends_register(app, monkeypatch):
    monkeypatch.setenv("INSTALL_ID", "install-abc")
    monkeypatch.setenv("INSTALL_TOKEN", "token-xyz")
    fake = FakePost(200)
    monkeypatch.setattr(telemetry, "_post", fake)
    with app.app_context():
        ok = telemetry.maybe_check_in(force=True)
        assert ok is True
    assert len(fake.calls) == 1
    url, payload, _ = fake.calls[0]
    assert url == "https://license.iveras.com/api/register"
    assert payload["install_id"] == "install-abc"
    assert payload["info"]["hostname"]
    with app.app_context():
        assert telemetry._registered_id() == "install-abc"
        assert telemetry._last_check() is not None


def test_heartbeat_sent_after_registered(app, monkeypatch):
    monkeypatch.setenv("INSTALL_ID", "install-abc")
    monkeypatch.setenv("INSTALL_TOKEN", "token-xyz")
    fake = FakePost(200)
    monkeypatch.setattr(telemetry, "_post", fake)
    with app.app_context():
        telemetry._mark_registered("install-abc")
        telemetry._set_last_check(float("inf"))
        assert telemetry.maybe_check_in() is False
        assert len(fake.calls) == 0
        ok = telemetry.maybe_check_in(force=True)
        assert ok is True
    assert fake.calls[0][0].endswith("/api/telemetry")


def test_unauthorized_clears_registration(app, monkeypatch):
    monkeypatch.setenv("INSTALL_ID", "install-abc")
    monkeypatch.setenv("INSTALL_TOKEN", "token-xyz")
    fake = FakePost(403)
    monkeypatch.setattr(telemetry, "_post", fake)
    with app.app_context():
        telemetry._mark_registered("install-abc")
        assert telemetry.maybe_check_in(force=True) is False
        assert telemetry._registered_id() != "install-abc"
    assert fake.calls[0][0].endswith("/api/telemetry")


def test_no_identity_no_network(app, monkeypatch):
    fake = FakePost(200)
    monkeypatch.setattr(telemetry, "_post", fake)
    with app.app_context():
        Setting.set("install_id", "", category="system")
        assert telemetry.get_install_id() is None
        assert telemetry.maybe_check_in(force=True) is False
    assert len(fake.calls) == 0


def test_disabled_no_network(app, monkeypatch):
    monkeypatch.setenv("INSTALL_ID", "install-abc")
    monkeypatch.setenv("INSTALL_TOKEN", "token-xyz")
    fake = FakePost(200)
    monkeypatch.setattr(telemetry, "_post", fake)
    with app.app_context():
        Setting.set("telemetry_enabled", "false", category="general")
        assert telemetry.maybe_check_in(force=True) is False
    assert len(fake.calls) == 0


def test_ensure_install_identity_generates(app, monkeypatch):
    with app.app_context():
        init_default_settings()
        install_id = telemetry.ensure_install_identity()
        assert install_id
        assert Setting.get("install_id") == install_id
        assert Setting.get("install_token")
        assert telemetry.get_install_id() == install_id


def test_server_url_setting_respected(app, monkeypatch):
    monkeypatch.setenv("INSTALL_ID", "install-abc")
    monkeypatch.setenv("INSTALL_TOKEN", "token-xyz")
    fake = FakePost(200)
    monkeypatch.setattr(telemetry, "_post", fake)
    with app.app_context():
        Setting.set(
            "telemetry_server_url", "https://license.example.test/", category="general"
        )
        telemetry.maybe_check_in(force=True)
    assert fake.calls[0][0] == "https://license.example.test/api/register"


def test_remote_calls_use_bearer_auth(app, monkeypatch):
    monkeypatch.setenv("INSTALL_ID", "install-abc")
    monkeypatch.setenv("INSTALL_TOKEN", "token-xyz")
    calls = {}

    def fake_post(url, **kwargs):
        calls["headers"] = kwargs.get("headers", {})
        calls["json"] = kwargs.get("json", {})
        return FakeResponse(200)

    monkeypatch.setattr(telemetry.requests, "post", fake_post)
    with app.app_context():
        ok = telemetry.maybe_check_in(force=True)
    assert ok is True
    assert calls["headers"]["Authorization"] == "Bearer token-xyz"
    assert calls["headers"]["X-Install-ID"] == "install-abc"
    assert calls["json"]["install_id"] == "install-abc"


def test_loop_skips_when_not_production(app, monkeypatch):
    started = []

    def fake_loop(app):
        started.append(app)

    monkeypatch.setattr(app, "testing", False)
    monkeypatch.setattr(telemetry, "_telemetry_loop", fake_loop)
    monkeypatch.setenv("FLASK_ENV", "development")
    with app.app_context():
        telemetry.init_telemetry(app)
    assert started == []


def test_loop_starts_in_production(app, monkeypatch):
    started = []

    def fake_loop(app):
        started.append(app)

    monkeypatch.setattr(app, "testing", False)
    monkeypatch.setattr(telemetry, "_telemetry_loop", fake_loop)
    monkeypatch.setenv("FLASK_ENV", "production")
    with app.app_context():
        telemetry.init_telemetry(app)
    assert started == [app]
