"""Tests for global Setting storage and the settings reset flow."""

from cms.models import Setting, init_default_settings


def test_default_settings_seeded(app):
    """Known API keys from the defaults list exist and are active/visible."""
    with app.app_context():
        init_default_settings()
        for key in (
            "brave_api_key",
            "pimeyes_api_key",
            "tineye_api_key",
            "picarta_api_key",
        ):
            row = Setting.query.filter_by(key=key).first()
            assert row is not None, f"{key} not seeded"
            assert row.is_active is True
            assert row.category == "api_keys"


def test_reset_clears_value_and_stays_visible(app):
    """Reset must clear the value and keep the row active + visible in the UI."""
    with app.app_context():
        init_default_settings()
        Setting.set(
            "picarta_api_key",
            "PICARTA-SECRET-123",
            category="api_keys",
            description="Picarta API",
        )
        row = Setting.query.filter_by(key="picarta_api_key").first()
        assert row.is_active is True
        assert row.value == "PICARTA-SECRET-123"

        # Mirror what the reset endpoint does (value cleared, row stays active).
        row.value = None
        row.is_encrypted = False
        row.is_active = True
        init_default_settings()

        row = Setting.query.filter_by(key="picarta_api_key").first()
        assert row.is_active is True
        assert row.category == "api_keys"
        assert not row.value
        visible = [
            s.key
            for s in Setting.query.filter_by(category="api_keys", is_active=True).all()
        ]
        assert "picarta_api_key" in visible


def test_init_default_settings_reactivates_deactivated_row(app):
    """A deactivated known default must come back to life on the next start."""
    with app.app_context():
        init_default_settings()
        row = Setting.query.filter_by(key="picarta_api_key").first()
        row.value = "stale-secret"
        row.is_active = False
        init_default_settings()

        row = Setting.query.filter_by(key="picarta_api_key").first()
        assert row.is_active is True
        assert row.category == "api_keys"
        visible = [
            s.key
            for s in Setting.query.filter_by(category="api_keys", is_active=True).all()
        ]
        assert "picarta_api_key" in visible
