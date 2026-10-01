"""Tests for global Setting storage and the settings reset flow."""

from cms.models import (
    AuditLog,
    Setting,
    Tenant,
    TenantSetting,
    User,
    db,
    init_default_settings,
)


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


def test_tenant_settings_are_scoped_to_current_tenant(auth_client, app):
    with app.app_context():
        from cms.models import User

        admin = User.query.filter_by(username="admin").first()
        other = Tenant(
            name="Other settings tenant",
            slug="other-settings-tenant",
            join_code="other-settings-code",
        )
        db.session.add(other)
        db.session.flush()
        TenantSetting.set("own_setting", "own", tenant_id=admin.tenant_id)
        TenantSetting.set("foreign_setting", "foreign", tenant_id=other.id)
        db.session.commit()

    response = auth_client.get("/cms/api/tenant-settings")
    assert response.status_code == 200
    keys = {item["key"] for item in response.get_json()["settings"]}
    assert "own_setting" in keys
    assert "foreign_setting" not in keys


def test_tenant_integration_settings_are_seeded_without_global_values(
    auth_client, app
):
    response = auth_client.get("/cms/tenant-settings")
    assert response.status_code == 200

    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        rows = TenantSetting.query.filter_by(tenant_id=admin.tenant_id).all()
        values = {row.key: row.value for row in rows}

    assert {
        "webhook_urls",
        "webhook_secret",
        "webhook_url",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_from_number",
        "twilio_whatsapp_from",
    }.issubset(values)
    assert all(value is None for value in values.values())


def test_non_admin_cannot_read_or_write_settings(app, auth_client):
    with app.app_context():
        viewer = User(
            username="settings-viewer",
            email="settings-viewer@localhost",
            full_name="Settings Viewer",
            role="viewer",
            tenant_id=User.query.filter_by(username="admin").first().tenant_id,
            is_active=True,
        )
        viewer.set_password("Test1234!")
        db.session.add(viewer)
        db.session.commit()
        viewer_id = viewer.id

    with auth_client.session_transaction() as sess:
        sess["_user_id"] = str(viewer_id)
        sess["_fresh"] = True

    assert auth_client.get("/cms/tenant-settings").status_code == 403
    assert auth_client.get("/cms/api/tenant-settings").status_code == 403
    assert auth_client.get("/cms/settings?category=api_keys").status_code == 403
    assert auth_client.get("/cms/api/platform-settings").status_code == 403


def test_tenant_settings_update_cannot_cross_tenant(auth_client, app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        admin_tenant_id = admin.tenant_id
        other = Tenant(
            name="Other update tenant",
            slug="other-update-tenant",
            join_code="other-update-tenant-code",
        )
        db.session.add(other)
        db.session.flush()
        foreign = TenantSetting.set(
            "webhook_secret",
            "unchanged",
            tenant_id=other.id,
            category="integrations",
            encrypt=False,
        )
        own = TenantSetting.set(
            "webhook_secret",
            "own-secret",
            tenant_id=admin_tenant_id,
            category="integrations",
            encrypt=True,
        )
        foreign_id = foreign.id
        own_id = own.id

    response = auth_client.post(
        "/cms/api/tenant-settings",
        json={
            "settings": [
                {"id": foreign_id, "value": "must-not-change"},
                {"id": own_id, "value": "updated-own-secret"},
            ]
        },
    )
    assert response.status_code == 200
    assert response.get_json()["saved"] == 1

    with app.app_context():
        assert db.session.get(TenantSetting, foreign_id).value == "unchanged"
        assert TenantSetting.get("webhook_secret", tenant_id=admin_tenant_id) == (
            "updated-own-secret"
        )

        audit = (
            AuditLog.query.filter_by(
                action="tenant_setting_updated", entity_id=own_id
            )
            .order_by(AuditLog.timestamp.desc())
            .first()
        )
        assert audit is not None
        assert audit.tenant_id == admin_tenant_id
        assert "updated-own-secret" not in str(audit.changes_made)


def test_encrypted_tenant_setting_is_not_rendered_or_cleared_by_blank_save(
    auth_client, app
):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        setting = TenantSetting.set(
            "twilio_auth_token",
            "do-not-render-this",
            tenant_id=admin.tenant_id,
            category="integrations",
            encrypt=True,
        )
        setting_id = setting.id
        tenant_id = admin.tenant_id

    page = auth_client.get("/cms/tenant-settings")
    assert page.status_code == 200
    assert b"do-not-render-this" not in page.data

    response = auth_client.post(
        "/cms/api/tenant-settings",
        json={"settings": [{"id": setting_id, "value": ""}]},
    )
    assert response.status_code == 200
    assert response.get_json()["saved"] == 0

    with app.app_context():
        assert TenantSetting.get("twilio_auth_token", tenant_id=tenant_id) == (
            "do-not-render-this"
        )


def test_tenant_settings_api_masks_encrypted_values(auth_client, app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        TenantSetting.set(
            "twilio_auth_token",
            "api-secret-must-not-leak",
            tenant_id=admin.tenant_id,
            category="integrations",
            encrypt=True,
        )

    response = auth_client.get("/cms/api/tenant-settings")
    assert response.status_code == 200
    item = next(
        entry
        for entry in response.get_json()["settings"]
        if entry["key"] == "twilio_auth_token"
    )
    assert item["value"] == "***MASKED***"
    assert "api-secret-must-not-leak" not in response.get_data(as_text=True)
