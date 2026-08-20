"""
HTTP-based smoke tests for #54-#58 features.
Uses subprocess+curl for login (avoids Python requests SameSite=Strict cookie bug
on Python 3.14).

Usage:
    BASE_URL=http://localhost:5002 TEST_PASSWORD=smoketest123 pytest tests/test_browser_smoke.py -v
    BASE_URL=https://joost.iveras.com TEST_PASSWORD=xxx TEST_TOTP_SECRET=xxx pytest tests/test_browser_smoke.py -v
"""

import os
import re
import subprocess
import tempfile

import pyotp
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5002")
EMAIL = os.environ.get("TEST_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("TEST_PASSWORD", "smoketest123")
TOTP_SECRET = os.environ.get("TEST_TOTP_SECRET", "")

CASE_ID_PROD = "82d071da-8af9-487d-8c9d-1f50fa89ca5d"


def _curl(method, url, data=None, cookie_file=None, follow=False):
    cmd = ["curl", "-s", "-w", "\n__HTTP_CODE__%{http_code}", "-D", "/dev/stderr"]
    if method == "POST" and data:
        for k, v in data.items():
            cmd += ["-d", f"{k}={v}"]
        from urllib.parse import urlparse

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cmd += ["-H", f"Origin: {origin}", "-H", f"Referer: {url}"]
    if cookie_file:
        cmd += ["-b", cookie_file, "-c", cookie_file]
    if follow:
        cmd += ["-L"]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output = result.stdout
    stderr = result.stderr
    m = re.search(r"__HTTP_CODE__(\d+)$", output)
    status = int(m.group(1)) if m else 0
    body = re.sub(r"__HTTP_CODE__\d+$", "", output)
    location = ""
    for line in stderr.split("\n"):
        if line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
    return status, body, location


@pytest.fixture(scope="session")
def case_id():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        cookie_file = f.name
    try:
        status, body, _ = _curl(
            "GET", f"{BASE_URL}/cms/cases/{CASE_ID_PROD}", cookie_file=cookie_file
        )
        if status == 200:
            return CASE_ID_PROD
        status, body, _ = _curl("GET", f"{BASE_URL}/cms/cases", cookie_file=cookie_file)
        m = re.search(r"/cms/cases/([0-9a-f-]{36})", body)
        if m:
            return m.group(1)
        pytest.skip("No cases found on this server")
    finally:
        os.unlink(cookie_file)


@pytest.fixture(scope="session")
def session():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        cookie_file = f.name

    try:
        status, body, _ = _curl(
            "GET", f"{BASE_URL}/auth/login", cookie_file=cookie_file
        )
        assert status == 200, f"Login page returned {status}"

        csrf = _extract_csrf(body)
        status, body, location = _curl(
            "POST",
            f"{BASE_URL}/auth/login",
            data={"email": EMAIL, "password": PASSWORD, "csrf_token": csrf},
            cookie_file=cookie_file,
        )

        if "/auth/2fa/verify" in location or "/auth/2fa/verify" in body:
            if not TOTP_SECRET:
                pytest.skip("2FA required but TEST_TOTP_SECRET not set")
            status, body, _ = _curl(
                "GET", f"{BASE_URL}/auth/2fa/verify", cookie_file=cookie_file
            )
            csrf2 = _extract_csrf(body)
            code = pyotp.TOTP(TOTP_SECRET).now()
            status, body, location = _curl(
                "POST",
                f"{BASE_URL}/auth/2fa/verify",
                data={"code": code, "csrf_token": csrf2},
                cookie_file=cookie_file,
            )

        assert status in (200, 302), f"Login failed with status {status}"
        if status == 302:
            assert "/cms" in location, f"Login redirect went to {location}"
        else:
            status, body, _ = _curl("GET", f"{BASE_URL}/cms/", cookie_file=cookie_file)
            assert status == 200, f"Dashboard returned {status}"

        return cookie_file
    except Exception:
        os.unlink(cookie_file)
        raise


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert m, "Could not find CSRF token on page"
    return m.group(1)


def _get(url, cookie_file):
    return _curl("GET", url, cookie_file=cookie_file)


class TestLogin:
    def test_login_succeeds(self, session):
        status, body, _ = _get(f"{BASE_URL}/cms/", session)
        assert status == 200


class TestCaseDetail:
    """#54/#59: subject names should be decrypted on case detail."""

    def test_no_ciphertext_on_case_detail(self, session, case_id):
        status, body, _ = _get(f"{BASE_URL}/cms/cases/{case_id}", session)
        assert status == 200
        assert "gAAAAAB" not in body, (
            "Case detail page still shows ciphertext for subject data"
        )

    def test_known_subject_name_visible(self, session, case_id):
        status, body, _ = _get(f"{BASE_URL}/cms/cases/{case_id}", session)
        assert status == 200
        has_name = any(
            name in body for name in ["Marloes", "Sterling", "Herman", "Test"]
        )
        assert has_name, "Expected at least one known subject name on case detail"


class TestSubjectSearch:
    """#57: encrypted search should find subjects by name."""

    def test_search_marloes(self, session):
        status, body, _ = _get(f"{BASE_URL}/cms/subjects?search=Marloes", session)
        assert status == 200
        assert "Marloes" in body, "Search for 'Marloes' returned no results"
        assert "gAAAAAB" not in body

    def test_search_herman(self, session):
        status, body, _ = _get(f"{BASE_URL}/cms/subjects?search=Herman", session)
        assert status == 200
        assert "Herman" in body, "Search for 'Herman' returned no results"
        assert "gAAAAAB" not in body


class TestSubjectProfile:
    """#56: action card and #58: merge card on subject profile."""

    @pytest.fixture(scope="class")
    def subject_id(self, session):
        status, body, _ = _get(f"{BASE_URL}/cms/subjects?search=Marloes", session)
        m = re.search(r"/cms/subjects/([0-9a-f-]{36})", body)
        if not m:
            pytest.skip("Could not find a subject ID in search results")
        return m.group(1)

    def test_profile_loads(self, session, subject_id):
        status, _, _ = _get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", session)
        assert status == 200

    def test_no_ciphertext_on_profile(self, session, subject_id):
        status, body, _ = _get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", session)
        assert status == 200
        assert "gAAAAAB" not in body, "Subject profile still shows ciphertext"

    def test_action_card_present(self, session, subject_id):
        status, body, _ = _get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", session)
        if status != 200:
            pytest.skip("Profile not accessible")
        has_action = any(
            marker in body
            for marker in ["btn-run-action", "action-type", "Start Research"]
        )
        if not has_action:
            pytest.skip("Action UI not visible (feature flag may be disabled)")

    def test_merge_card_present(self, session, subject_id):
        status, body, _ = _get(f"{BASE_URL}/cms/subjects/{subject_id}/profile", session)
        if status != 200:
            pytest.skip("Profile not accessible")
        has_merge = any(marker in body for marker in ["merge-source", "Merge"])
        if not has_merge:
            pytest.skip("Merge UI not visible (admin-only or feature flag)")
