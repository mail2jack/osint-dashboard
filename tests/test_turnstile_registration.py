from unittest.mock import Mock, patch

from cms.models import CustomerRegistrationRequest


def _registration_data(email="turnstile@example.test"):
    return {
        "registration_type": "other",
        "contact_name": "Turnstile Test",
        "email": email,
        "message": "Synthetische aanvraag voor de Turnstile-test.",
    }


def test_required_turnstile_rejects_missing_token(client):
    config = client.application.config
    config.update(
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
        TURNSTILE_REQUIRED=True,
        TURNSTILE_ALLOWED_HOSTNAMES="localhost",
        TURNSTILE_ACTION="customer-registration",
    )
    try:
        response = client.post("/portal/register", data=_registration_data())
        assert response.status_code == 400
        assert b"Bevestig eerst" in response.data
        assert CustomerRegistrationRequest.query.count() == 0
    finally:
        config.update(
            TURNSTILE_SITE_KEY="",
            TURNSTILE_SECRET_KEY="",
            TURNSTILE_REQUIRED=False,
            TURNSTILE_ALLOWED_HOSTNAMES="",
            TURNSTILE_ACTION="customer-registration",
        )


def test_valid_turnstile_token_allows_registration(client):
    config = client.application.config
    config.update(
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
        TURNSTILE_REQUIRED=True,
    )
    response_body = {
        "success": True,
        "hostname": "localhost",
        "action": "customer-registration",
    }
    try:
        with patch("cms.services.turnstile.requests.post") as post:
            post.return_value = Mock(status_code=200, json=lambda: response_body)
            response = client.post(
                "/portal/register",
                data={**_registration_data("turnstile-valid@example.test"), "cf-turnstile": "valid-token"},
            )
        assert response.status_code == 200
        assert CustomerRegistrationRequest.query.filter_by(
            email="turnstile-valid@example.test"
        ).one()
        post.assert_called_once()
    finally:
        config.update(
            TURNSTILE_SITE_KEY="",
            TURNSTILE_SECRET_KEY="",
            TURNSTILE_REQUIRED=False,
            TURNSTILE_ALLOWED_HOSTNAMES="",
            TURNSTILE_ACTION="customer-registration",
        )
