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

import re
from pathlib import Path

import pytest

from cms.models import User

STATE_MUTATING_ROUTES = [
    "/cms/api/vessel/update-subject",
    "/cms/api/findings/from-vessel",
    "/cms/subjects/00000000-0000-0000-0000-000000000000/update-from-rdw",
    "/cms/api/findings/from-interpol",
    "/api/username/rapidapi",
]

EXPECTED_EXEMPT_FUNCTIONS = {
    "csp_report",
    "person_search_stream",
    "person_search_json",
    "email_lookup",
    "ip_lookup",
    "domain_lookup",
    "openkvk_lookup",
    "webcam_lookup",
    "hibp_check",
    "username_search_stream",
    "email_search_stream",
    "email_holehe",
    "email_combined",
    "email_cross_validated",
    "username_search",
    "ai_summarize",
    "ai_analyze_query",
    "ai_enrich_profile",
    "phone_lookup_stored",
    "phone_lookup",
    "webhook",
    "full_text_search",
    "email_check",
    "check_policie_data",
    "kvk_lookup",
    "kadaster_lookup",
    "politiebureau_lookup",
    "check_rdw_vehicle",
    "vessel_lookup",
}


def _scan_exempt_functions():
    root = Path(__file__).resolve().parents[1]
    found = set()
    for py in root.rglob("*.py"):
        if ".venv" in str(py) or "node_modules" in str(py):
            continue
        lines = py.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "@csrf.exempt":
                for j in range(i + 1, min(i + 12, len(lines))):
                    m = re.match(r"\s*def\s+(\w+)", lines[j])
                    if m:
                        found.add(m.group(1))
                        break
    return found


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


@pytest.mark.parametrize("path", STATE_MUTATING_ROUTES)
def test_cross_origin_post_without_csrf_is_rejected(csrf_client, path):
    """A cross-origin POST (evil Origin header, no token) is still rejected.

    CSRF protection must not rely on the Origin header; enforcement comes from
    the required token. The same CSRF ``400`` signature proves the token gate
    ran first, even with a hostile ``Origin`` present.
    """
    resp = csrf_client.post(
        path,
        json={"kenteken": "AB-123-K"},
        headers={"Origin": "https://evil.example"},
    )
    body = resp.get_data(as_text=True)
    assert "Bad request" in body, (resp.status_code, body)
    assert "Validation failed" not in body, (resp.status_code, body)


def test_exempt_catalog_allowlist_in_sync():
    """Every @csrf.exempt must be accounted for in the catalog allowlist.

    Adding or removing an exemption without updating
    ``docs/CSRF_EXEMPT_CATALOG.md`` AND ``EXPECTED_EXEMPT_FUNCTIONS`` fails
    this guard, forcing a conscious catalog decision.
    """
    assert _scan_exempt_functions() == EXPECTED_EXEMPT_FUNCTIONS


def test_csp_report_is_exempt_by_design(csrf_client):
    """CSP violation reports arrive from the browser without a CSRF token."""
    resp = csrf_client.post(
        "/csp-report",
        json={"csp-report": {"document-uri": "https://joost.iveras.com/"}},
    )
    body = resp.get_data(as_text=True)
    assert "Bad request" not in body, (resp.status_code, body)


def test_stripe_webhook_is_exempt_but_signature_gated(csrf_client):
    """Stripe webhook is exempt from CSRF; without a valid signature it is
    still rejected by Stripe's own check (never by the CSRF ``400``)."""
    resp = csrf_client.post("/stripe/webhook", json={})
    body = resp.get_data(as_text=True)
    assert "Bad request" not in body, (resp.status_code, body)


def test_production_session_cookie_is_strict():
    from cms.config import ProductionConfig

    assert ProductionConfig.SESSION_COOKIE_SAMESITE == "Strict"
    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.WTF_CSRF_ENABLED is True


def test_no_config_uses_samesite_none():
    from cms.config import Config, DevelopmentConfig, ProductionConfig

    for cls in (Config, DevelopmentConfig, ProductionConfig):
        assert cls.SESSION_COOKIE_SAMESITE != "None"
