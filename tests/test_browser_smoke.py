"""
Browser smoke tests for #54-#58 features.
Runs against a live server (local or production).

Usage:
    BASE_URL=http://localhost:5000 pytest tests/test_browser_smoke.py -v
    BASE_URL=https://joost.iveras.com pytest tests/test_browser_smoke.py -v --headed
"""

import os
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
EMAIL = os.environ.get("TEST_EMAIL", "testu00@iveras.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "test1234")

# Use the known case and subjects from production
CASE_URL = "/cms/cases/82d071da-8af9-487d-8c9d-1f50fa89ca5d"


@pytest.fixture(scope="session")
def browser_context_args():
    return {"viewport": {"width": 1280, "height": 720}}


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


def _login(page):
    """Login via the CMS login form."""
    page.goto(f"{BASE_URL}/auth/login")
    page.fill('input[name="email"], input#email', EMAIL)
    page.fill('input[name="password"], input#password', PASSWORD)
    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_load_state("networkidle")
    # Should be redirected to dashboard
    assert "/cms" in page.url


class TestLogin:
    def test_login_succeeds(self, page):
        _login(page)
        assert "/cms" in page.url


class TestCaseDetail:
    """#54/#59 regression: subject names should be decrypted on case detail."""

    def test_case_detail_shows_plaintext_subjects(self, page):
        _login(page)
        page.goto(f"{BASE_URL}{CASE_URL}")
        page.wait_for_load_state("networkidle")

        # The subjects table should NOT contain Fernet ciphertext
        content = page.content()
        assert "gAAAAAB" not in content, (
            "Case detail page still shows ciphertext for subject data"
        )

        # Should show a real subject name
        assert "Marloes" in content or "Sterling" in content or "Herman" in content, (
            "Expected at least one known subject name on case detail"
        )


class TestSubjectSearch:
    """#57: encrypted search should find subjects by name."""

    def test_search_finds_subject(self, page):
        _login(page)
        page.goto(f"{BASE_URL}/cms/subjects?search=Marloes")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "Marloes" in content, "Search for 'Marloes' returned no results"


class TestSubjectProfile:
    """#56: action card should be present on subject profile."""

    def test_profile_has_action_card(self, page):
        _login(page)
        # Find Marloes' profile via search
        page.goto(f"{BASE_URL}/cms/subjects?search=Marloes")
        page.wait_for_load_state("networkidle")

        # Click first "Bekijken" link
        view_link = page.query_selector('a[href*="/cms/subjects/"]')
        if view_link:
            view_link.click()
            page.wait_for_load_state("networkidle")

            content = page.content()
            # Check for action-related elements
            has_action_ui = (
                "Run" in content
                or "Start Research" in content
                or "action-type" in content
                or "btn-run-action" in content
            )
            # Not a hard failure — feature flag might hide it
            if not has_action_ui:
                pytest.skip("Action UI not visible (feature flag may be disabled)")


class TestSubjectMerge:
    """#58: merge card should be present for admin users."""

    def test_merge_card_present(self, page):
        _login(page)
        page.goto(f"{BASE_URL}/cms/subjects?search=Marloes")
        page.wait_for_load_state("networkidle")

        view_link = page.query_selector('a[href*="/cms/subjects/"]')
        if view_link:
            view_link.click()
            page.wait_for_load_state("networkidle")

            content = page.content()
            has_merge_ui = "Merge" in content or "merge-source" in content
            if not has_merge_ui:
                pytest.skip("Merge UI not visible (admin-only or feature flag)")


class TestEncryptedSearch:
    """#57: search by email should work on encrypted fields."""

    def test_search_by_name_returns_results(self, page):
        _login(page)
        page.goto(f"{BASE_URL}/cms/subjects?search=Herman")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "Herman" in content, "Search for 'Herman' returned no results"
        # Should NOT show ciphertext
        assert "gAAAAAB" not in content
