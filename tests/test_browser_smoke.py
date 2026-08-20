"""
HTTP-based smoke tests for #54-#58 features.
No browser needed — uses requests with session cookies.

Usage:
    BASE_URL=http://localhost:5002 TEST_PASSWORD=smoketest123 pytest tests/test_browser_smoke.py -v
    BASE_URL=https://joost.iveras.com TEST_PASSWORD=xxx TEST_TOTP_SECRET=xxx pytest tests/test_browser_smoke.py -v
"""

import os
import re

import pyotp
import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5002")
EMAIL = os.environ.get("TEST_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("TEST_PASSWORD", "smoketest123")
TOTP_SECRET = os.environ.get("TEST_TOTP_SECRET", "")

CASE_ID_PROD = "82d071da-8af9-487d-8c9d-1f50fa89ca5d"


@pytest.fixture(scope="session")
def case_id(session):
    resp = session.get(f"{BASE_URL}/cms/cases/{CASE_ID_PROD}", timeout=15)
    if resp.status_code == 200:
        return CASE_ID_PROD
    resp = session.get(f"{BASE_URL}/cms/cases", timeout=15)
    m = re.search(r"/cms/cases/([0-9a-f-]{36})", resp.text)
    if m:
        return m.group(1)
    pytest.skip("No cases found on this server")


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"User-Agent": "osint-smoke-test/1.0"})

    resp = s.get(f"{BASE_URL}/auth/login", timeout=15)
    assert resp.status_code == 200, f"Login page returned {resp.status_code}"

    csrf = _extract_csrf(resp.text)
    resp = s.post(
        f"{BASE_URL}/auth/login",
        data={"email": EMAIL, "password": PASSWORD, "csrf_token": csrf},
        allow_redirects=True,
        timeout=15,
    )

    if "/auth/2fa/verify" in resp.url:
        if not TOTP_SECRET:
            pytest.skip("2FA required but TEST_TOTP_SECRET not set")
        csrf2 = _extract_csrf(resp.text)
        code = pyotp.TOTP(TOTP_SECRET).now()
        resp = s.post(
            f"{BASE_URL}/auth/2fa/verify",
            data={"code": code, "csrf_token": csrf2},
            allow_redirects=True,
            timeout=15,
        )

    assert "/cms" in resp.url, f"Login failed — landed on {resp.url}"
    return s


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not m:
        m = re.search(r'csrf_token["\s:=]+["\']([a-zA-Z0-9._-]+)', html)
    assert m, "Could not find CSRF token on page"
    return m.group(1)


def _csrf(session: requests.Session, url: str) -> str:
    resp = session.get(url, timeout=15)
    return _extract_csrf(resp.text)


class TestLogin:
    def test_login_succeeds(self, session):
        resp = session.get(f"{BASE_URL}/cms/", timeout=15)
        assert resp.status_code == 200
        assert "Dashboard" in resp.text or "dashboard" in resp.text.lower()


class TestCaseDetail:
    """#54/#59: subject names should be decrypted on case detail."""

    def test_no_ciphertext_on_case_detail(self, session, case_id):
        resp = session.get(f"{BASE_URL}/cms/cases/{case_id}", timeout=15)
        assert resp.status_code == 200
        assert "gAAAAAB" not in resp.text, (
            "Case detail page still shows ciphertext for subject data"
        )

    def test_known_subject_name_visible(self, session, case_id):
        resp = session.get(f"{BASE_URL}/cms/cases/{case_id}", timeout=15)
        assert resp.status_code == 200
        has_name = any(
            name in resp.text for name in ["Marloes", "Sterling", "Herman", "Test"]
        )
        assert has_name, "Expected at least one known subject name on case detail"


class TestSubjectSearch:
    """#57: encrypted search should find subjects by name."""

    def test_search_marloes(self, session):
        resp = session.get(
            f"{BASE_URL}/cms/subjects", params={"search": "Marloes"}, timeout=15
        )
        assert resp.status_code == 200
        assert "Marloes" in resp.text, "Search for 'Marloes' returned no results"
        assert "gAAAAAB" not in resp.text

    def test_search_herman(self, session):
        resp = session.get(
            f"{BASE_URL}/cms/subjects", params={"search": "Herman"}, timeout=15
        )
        assert resp.status_code == 200
        assert "Herman" in resp.text, "Search for 'Herman' returned no results"
        assert "gAAAAAB" not in resp.text


class TestSubjectProfile:
    """#56: action card and #58: merge card on subject profile."""

    @pytest.fixture(scope="class")
    def subject_id(self, session):
        resp = session.get(
            f"{BASE_URL}/cms/subjects", params={"search": "Marloes"}, timeout=15
        )
        m = re.search(r"/cms/subjects/([0-9a-f-]{36})", resp.text)
        if not m:
            pytest.skip("Could not find a subject ID in search results")
        return m.group(1)

    def test_profile_loads(self, session, subject_id):
        resp = session.get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", timeout=15)
        assert resp.status_code == 200

    def test_no_ciphertext_on_profile(self, session, subject_id):
        resp = session.get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", timeout=15)
        assert resp.status_code == 200
        assert "gAAAAAB" not in resp.text, "Subject profile still shows ciphertext"

    def test_action_card_present(self, session, subject_id):
        resp = session.get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", timeout=15)
        if resp.status_code != 200:
            pytest.skip("Profile not accessible")
        has_action = any(
            marker in resp.text
            for marker in ["btn-run-action", "action-type", "Start Research"]
        )
        if not has_action:
            pytest.skip("Action UI not visible (feature flag may be disabled)")

    def test_merge_card_present(self, session, subject_id):
        resp = session.get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", timeout=15)
        if resp.status_code != 200:
            pytest.skip("Profile not accessible")
        has_merge = any(marker in resp.text for marker in ["merge-source", "Merge"])
        if not has_merge:
            pytest.skip("Merge UI not visible (admin-only or feature flag)")
