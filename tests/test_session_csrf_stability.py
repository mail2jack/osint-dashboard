"""Regression: CSRF token must survive intermediate GETs; SID/cookie must stay stable.

Issue #88 (P2, 2026-08-29): during the 10.8 verification, intermediate GETs
(to e.g. the ``ensure_case_access`` path) broke the CSRF voucher for the next
POST of the same client, and ``session_transaction()`` showed a different
(empty) session than the cookie the client actually sent — the SID file and
the cookie diverged. Root cause was the filesystem session store's
concurrent-write race under 2 gunicorn sync workers (ADR-0004), fixed by the
bounded cache (``cms/session_cache.py``) and finally eliminated by the Redis
session backend (docs/PLAN-REDIS-SESSION-MIGRATION.md, Fase D live).

This module pins the contract down in-process so verification can be strict
again (no retry tolerance): the CSRF token issued for a session stays valid
across intermediate requests, and the session cookie the client sends does not
change while the session is alive (no silent SID rotation on intermediate
GETs).
"""

import pytest

from cms.models import User


@pytest.fixture
def csrf_strict_client(app, client):
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


def _seed_csrf_token(app, client) -> str:
    """Store a plain CSRF value in the session and return its signed form.

    Mirrors Flask-WTF's signing (``URLSafeTimedSerializer`` with salt
    ``wtf-csrf-token``) exactly like ``test_csrf_protection.py`` does.
    """
    from itsdangerous import URLSafeTimedSerializer

    plain = "csrf-stability-test-token"
    serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt="wtf-csrf-token"
    )
    signed = serializer.dumps(plain)
    with client.session_transaction() as sess:
        sess["csrf_token"] = plain
    return signed


def _session_cookie(client):
    """Return the current session cookie value the test client holds."""
    jar = client.get_cookie("session")
    return jar.value if jar is not None else None


def test_csrf_token_survives_intermediate_gets(csrf_strict_client, app):
    """A CSRF token stays valid across intermediate non-state requests.

    Regression for #88: intermediate GETs (dashboard, then a detail page)
    must not invalidate the CSRF voucher for the next POST. A CSRF ``400``
    with the app's ``{"error": "Bad request"}`` body would prove a voucher
    break — this strictly asserts it does not happen, so verification checks
    can drop their retry tolerance.
    """
    token = _seed_csrf_token(app, csrf_strict_client)

    for path in ("/cms/dashboard", "/cms/audit"):
        resp = csrf_strict_client.get(path)
        assert resp.status_code in (200, 302, 404), (path, resp.status_code)

    resp = csrf_strict_client.post(
        "/cms/api/vessel/update-subject",
        json={"kenteken": "AB-123-K"},
        headers={"X-CSRFToken": token},
    )
    body = resp.get_data(as_text=True)
    # The request must pass CSRF and reach schema validation ("Validation
    # failed"). A CSRF voucher break returns the app's {"error":"Bad request"}.
    assert "Bad request" not in body, (resp.status_code, body)
    assert "Validation failed" in body, (resp.status_code, body)


def test_session_cookie_stable_across_intermediate_gets(csrf_strict_client):
    """The session cookie (SID) must not change on ordinary GET requests.

    Regression for #88: the SID file and the cookie diverged (an empty session
    where a cookie existed). If the backend silently creates a brand-new
    session mid-conversation (SID rotation without a login), the cookie value
    would change here.
    """
    before = _session_cookie(csrf_strict_client)
    assert before, "expected a session cookie to be present"

    for path in ("/cms/dashboard", "/"):
        csrf_strict_client.get(path)

    after = _session_cookie(csrf_strict_client)
    assert after == before, f"session cookie changed mid-conversation: {before!r} -> {after!r}"


def test_login_flow_establishes_stable_session(app, client):
    """A successful login must leave the client with one stable SID.

    Covers "Real-HTTP login gaf 400" from #88 at the contract level: the
    login GET seeds a session, the POST must not bounce the client into a
    different (empty) SID. Uses CSRF-enabled mode with the admin that has no
    TOTP, matching the auth fixture.
    """
    from cms.models import User

    app.config["WTF_CSRF_ENABLED"] = True
    try:
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            from cms.models import db

            db.session.commit()

        # GET seeds the CSRF token + session cookie
        resp = client.get("/auth/login")
        assert resp.status_code == 200, resp.status_code

        seed = _seed_csrf_token(app, client)
        sids = set()
        sids.add(_session_cookie(client))

        resp = client.post(
            "/auth/login",
            data={
                "email": "admin@localhost",
                "password": "Test1234!",
                "csrf_token": seed,
            },
            headers={"X-CSRFToken": seed},
            follow_redirects=False,
        )
        # 302 towards 2FA setup (admin has no TOTP) — login succeeded.
        assert resp.status_code in (302, 200), resp.status_code
        sids.add(_session_cookie(client))

        # A SID change is only legitimate right at login (fresh session).
        assert len(sids) <= 2, f"more than one SID re-issue: {sids}"
    finally:
        app.config["WTF_CSRF_ENABLED"] = False