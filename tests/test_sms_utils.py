"""Tenant isolation tests for SMS/WhatsApp configuration."""

from cms.models import Tenant, TenantSetting, User, db


def test_twilio_config_is_tenant_scoped(app):
    from cms.sms_utils import _get_twilio_config

    with app.app_context():
        tenant_a = User.query.filter_by(username="admin").first().tenant_id
        tenant_b = Tenant(
            name="Twilio tenant B",
            slug="twilio-tenant-b",
            join_code="twilio-tenant-b-code",
        )
        db.session.add(tenant_b)
        db.session.flush()
        TenantSetting.set("twilio_account_sid", "AC-A", tenant_id=tenant_a)
        TenantSetting.set("twilio_auth_token", "TOKEN-A", tenant_id=tenant_a)
        TenantSetting.set("twilio_account_sid", "AC-B", tenant_id=tenant_b.id)
        TenantSetting.set("twilio_auth_token", "TOKEN-B", tenant_id=tenant_b.id)

        assert _get_twilio_config(tenant_a)["account_sid"] == "AC-A"
        assert _get_twilio_config(tenant_a)["auth_token"] == "TOKEN-A"
        assert _get_twilio_config(tenant_b.id)["account_sid"] == "AC-B"
        assert _get_twilio_config(tenant_b.id)["auth_token"] == "TOKEN-B"


def test_twilio_config_without_tenant_is_empty(app):
    from cms.sms_utils import _get_twilio_config

    with app.app_context():
        assert _get_twilio_config("missing-tenant")["account_sid"] == ""
