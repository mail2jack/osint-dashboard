"""Tenant isolation tests for legacy notification webhook lookup."""

from unittest.mock import MagicMock, patch

from cms.models import Tenant, TenantSetting, User, db


def test_legacy_webhook_is_tenant_scoped(app):
    from cms.notifications import send_webhook

    with app.app_context():
        tenant_a = User.query.filter_by(username="admin").first().tenant_id
        tenant_b = Tenant(
            name="Legacy webhook tenant B",
            slug="legacy-webhook-tenant-b",
            join_code="legacy-webhook-tenant-b-code",
        )
        db.session.add(tenant_b)
        db.session.flush()
        TenantSetting.set("webhook_url", "https://tenant-a.example/", tenant_id=tenant_a)
        TenantSetting.set("webhook_url", "https://tenant-b.example/", tenant_id=tenant_b.id)

        response = MagicMock()
        response.raise_for_status.return_value = None
        with patch("httpx.post", return_value=response) as post:
            assert send_webhook("case.created", {"id": "1"}, tenant_id=tenant_a)
            assert post.call_args.args[0] == "https://tenant-a.example/"

