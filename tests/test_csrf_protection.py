"""CSRF regression tests.

The CMS blueprint enables Flask-WTF ``CSRFProtect`` globally. Most state-
mutating routes rely on it, but a handful were marked ``@csrf.exempt`` while
also mutating data and being reachable from the session-authenticated browser
frontend (the ``@api_key_required`` decorator is bypassed for session-authed
users, so an exemption left those endpoints open to cross-site requests).

These tests assert that the five state-mutating routes no longer accept
requests without a valid CSRF token:

- ``/cms/api/vessel/update-subject``
- ``/cms/api/findings/from-vessel``
- ``/cms/subjects/<id>/update-from-rdw``
- ``/cms/api/findings/from-interpol``
- ``/api/username/rapidapi``

Under the default test fixture CSRF is disabled (``WTF_CSRF_ENABLED=False``),
so these tests re-enable it locally to exercise the enforcement path.

The reject-vs-accept distinction relies on CSRFProtect running before schema
validation: a tokenless request is cut off with the app's CSRF ``400`` body
``{"error": "Bad request"}``, whereas a token-bearing request passes CSRF and
(for these dummy payloads) is then rejected by schema validation as
``{"error": "Validation failed"}``.
"""

import pytest

from cms.models import User

STATE_MUTATING_ROUTES = [
    "/cms/api/vessel/update-subject",
    "/cms/api/findings/from-vessel",
    "/cms/subjects/00000000-0000-0000-0000-000000000000/update-from-rdw",
    "/cms/api/findings/from-interpol",
    "/api/username/rapidapi",
]


@pytest.fixture
def csrf_client(app, client):
    """Enable CSRF enforcement and log the admin user into the session."""
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        admin_id = str(admin.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = admin_id
        sess["_fresh"] = True
        sess["_remember"] = "set"
    yield client
    app.config["WTF_CSRF_ENABLED"] = False


def _csrf_token(app, client) -> str:
    """Seed the client session with a plain token and return its signed form.

    ``validate_csrf`` loads the submitted (signed) token and compares the
    result against ``session['csrf_token']``. We mirror that exactly: store a
    plain value in the session and issue the ``URLSafeTimedSerializer``-signed
    form (the same serializer Flask-WTF uses) as the ``X-CSRFToken`` header.
    """
    from itsdangerous import URLSafeTimedSerializer

    plain = "csrf-test-token"
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="wtf-csrf-token")
    signed = serializer.dumps(plain)
    with client.session_transaction() as sess:
        sess["csrf_token"] = plain
    return signed


@pytest.mark.parametrize("path", STATE_MUTATING_ROUTES)
def test_state_mutating_routes_reject_without_csrf(csrf_client, path):
    """A cross-site (no token) POST must be rejected by CSRF.

    ``CSRFProtect`` runs before the route's schema validation. A tokenless
    request is rejected with the app's CSRF ``400`` whose body is
    ``{"error": "Bad request"}``, whereas a request that passes CSRF goes on to
    schema validation (``{"error": "Validation failed"}``). Asserting the CSRF
    signature specifically proves enforcement (not just "some 400").
    """
    resp = csrf_client.post(path, json={"kenteken": "AB-123-K"})
    body = resp.get_data(as_text=True)
    assert "Bad request" in body, (resp.status_code, body)
    assert "Validation failed" not in body, (resp.status_code, body)


@pytest.mark.parametrize("path", STATE_MUTATING_ROUTES)
def test_state_mutating_routes_accept_with_valid_csrf(csrf_client, app, path):
    """With a valid X-CSRFToken header, CSRF does not reject the request.

    The request proceeds past CSRF into the route handler (schema validation,
    an early ``"Username required"`` guard, etc.) instead of being cut off by
    CSRF (``{"error": "Bad request"}``). The CSRF rejection body is the one
    signal common to every endpoint, so its absence proves CSRF passed.
    """
    token = _csrf_token(app, csrf_client)
    resp = csrf_client.post(
        path,
        json={"kenteken": "AB-123-K"},
        headers={"X-CSRFToken": token},
    )
    body = resp.get_data(as_text=True)
    assert "Bad request" not in body, (resp.status_code, body)
