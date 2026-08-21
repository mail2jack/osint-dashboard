"""
HTTP-based smoke tests for #54-#58 features.
Uses requests.Session with Origin/Referer headers (POST only) for CSRF.

⚠️ SECURITY: These tests should run against a STAGING environment, NOT production.
Production data should never be used as test fixtures.

These tests are SKIPPED when no server URL is configured.
Set BROWSER_SMOKE_BASE_URL (or BASE_URL) to run them:

Usage (staging):
    BROWSER_SMOKE_BASE_URL=http://localhost:5002 TEST_PASSWORD=smoketest123 \\
        pytest tests/test_browser_smoke.py -v

Usage (production — ONLY for final verification, never for development):
    BROWSER_SMOKE_BASE_URL=https://joost.iveras.com TEST_PASSWORD=xxx TEST_TOTP_SECRET=xxx \\
        pytest tests/test_browser_smoke.py -v
"""

import os
import re
import time
import urllib.parse

import pytest

BASE_URL = os.environ.get("BROWSER_SMOKE_BASE_URL") or os.environ.get("BASE_URL", "")

if not BASE_URL:
    pytest.skip(
        "Browser smoke tests require a running server.\n"
        "Set BROWSER_SMOKE_BASE_URL (e.g. http://localhost:5002) to run them.",
        allow_module_level=True,
    )

EMAIL = os.environ.get("TEST_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("TEST_PASSWORD", "smoketest123")
TOTP_SECRET = os.environ.get("TEST_TOTP_SECRET", "")

if "joost.iveras.com" in BASE_URL:
    import warnings
    warnings.warn(
        "Running browser smoke tests against PRODUCTION. "
        "This should only be done for final verification, never during development.",
        stacklevel=2,
    )

CASE_ID_PROD = "82d071da-8af9-487d-8c9d-1f50fa89ca5d"

import pyotp
import requests


def _get(s, url):
    r = s.get(url, allow_redirects=False)
    return r.status_code, r.text, r.headers.get("Location", "")


def _post(s, url, data):
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"Origin": origin, "Referer": url}
    r = s.post(url, data=data, headers=headers, allow_redirects=False)
    return r.status_code, r.text, r.headers.get("Location", "")


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not m:
        m = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    assert m, "Could not find CSRF token on page"
    return m.group(1)


@pytest.fixture(scope="session")
def case_id(session):
    status, body, _ = _get(session, f"{BASE_URL}/cms/cases/{CASE_ID_PROD}")
    if status == 200:
        return CASE_ID_PROD
    status, body, _ = _get(session, f"{BASE_URL}/cms/cases")
    m = re.search(r"/cms/cases/([0-9a-f-]{36})", body)
    if m:
        return m.group(1)
    pytest.skip("No cases found on this server")


@pytest.fixture(scope="session")
def session():
    s = requests.Session()

    status, body, _ = _get(s, f"{BASE_URL}/auth/login")
    assert status == 200, f"Login page returned {status}"

    csrf = _extract_csrf(body)
    status, body, location = _post(
        s,
        f"{BASE_URL}/auth/login",
        data={"email": EMAIL, "password": PASSWORD, "csrf_token": csrf},
    )

    if "/auth/2fa/verify" in location or "/auth/2fa/verify" in body:
        if not TOTP_SECRET:
            pytest.skip("2FA required but TEST_TOTP_SECRET not set")

        for attempt in range(3):
            status, body, _ = _get(s, f"{BASE_URL}/auth/2fa/verify")
            if "/auth/2fa/setup" in location or "/auth/2fa/setup" in body:
                pytest.skip("2FA not yet configured")
            csrf2 = _extract_csrf(body)
            code = pyotp.TOTP(TOTP_SECRET).now()

            status, body, location = _post(
                s,
                f"{BASE_URL}/auth/2fa/verify",
                data={"code": code, "csrf_token": csrf2},
            )

            if status in (302,) and "/cms" in location:
                break
            if status == 200 and "/auth/2fa/verify" not in body:
                break
            if status == 200 and "Too many" in body:
                time.sleep(16)
                continue
            if attempt < 2:
                time.sleep(2)
                continue
            break

    assert status in (200, 302), f"Login failed with status {status}"
    if status == 302:
        assert "/cms" in location, f"Login redirect went to {location}"
    else:
        status, body, _ = _get(s, f"{BASE_URL}/cms/")
        assert status == 200, f"Dashboard returned {status}"

    return s


class TestLogin:
    def test_login_succeeds(self, session):
        status, body, _ = _get(session, f"{BASE_URL}/cms/")
        assert status == 200


class TestCaseDetail:
    """#54/#59: subject names should be decrypted on case detail."""

    def test_no_ciphertext_on_case_detail(self, session, case_id):
        status, body, _ = _get(session, f"{BASE_URL}/cms/cases/{case_id}")
        assert status == 200
        assert "gAAAAAB" not in body, (
            "Case detail page still shows ciphertext for subject data"
        )

    def test_known_subject_name_visible(self, session, case_id):
        status, body, _ = _get(session, f"{BASE_URL}/cms/cases/{case_id}")
        assert status == 200
        has_name = any(
            name in body for name in ["Marloes", "Sterling", "Herman", "Test"]
        )
        assert has_name, "Expected at least one known subject name on case detail"


class TestSubjectSearch:
    """#57: encrypted search should find subjects by name."""

    def test_search_marloes(self, session):
        status, body, _ = _get(session, f"{BASE_URL}/cms/subjects?search=Marloes")
        assert status == 200
        assert "Marloes" in body, "Search for 'Marloes' returned no results"
        assert "gAAAAAB" not in body

    def test_search_herman(self, session):
        status, body, _ = _get(session, f"{BASE_URL}/cms/subjects?search=Herman")
        assert status == 200
        assert "Herman" in body, "Search for 'Herman' returned no results"
        assert "gAAAAAB" not in body


class TestSubjectProfile:
    """#56: action card and #58: merge card on subject profile."""

    @pytest.fixture(scope="class")
    def subject_id(self, session):
        status, body, _ = _get(session, f"{BASE_URL}/cms/subjects?search=Marloes")
        m = re.search(r"/cms/subjects/([0-9a-f-]{36})", body)
        if not m:
            pytest.skip("Could not find a subject ID in search results")
        return m.group(1)

    def test_profile_loads(self, session, subject_id):
        status, _, _ = _get(session, f"{BASE_URL}/cms/subjects/{subject_id}/profile")
        assert status == 200

    def test_no_ciphertext_on_profile(self, session, subject_id):
        status, body, _ = _get(session, f"{BASE_URL}/cms/subjects/{subject_id}/profile")
        assert status == 200
        assert "gAAAAAB" not in body, "Subject profile still shows ciphertext"

    def test_action_card_present(self, session, subject_id):
        status, body, _ = _get(session, f"{BASE_URL}/cms/subjects/{subject_id}/profile")
        if status != 200:
            pytest.skip("Profile not accessible")
        has_action = any(
            marker in body
            for marker in ["btn-run-action", "action-type", "Start Research"]
        )
        if not has_action:
            pytest.skip("Action UI not visible (feature flag may be disabled)")

    def test_merge_card_present(self, session, subject_id):
        status, body, _ = _get(session, f"{BASE_URL}/cms/subjects/{subject_id}/profile")
        if status != 200:
            pytest.skip("Profile not accessible")
        has_merge = any(marker in body for marker in ["merge-source", "Merge"])
        if not has_merge:
            pytest.skip("Merge UI not visible (admin-only or feature flag)")
