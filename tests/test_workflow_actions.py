import json
from datetime import datetime
from unittest.mock import patch, MagicMock

from cms.workflow.research import (
    get_remaining_credits,
    _use_credit,
    _has_credits,
    _facebook_check,
    _tiktok_check,
    _instagram_check,
    _linkedin_check,
    _twitter_check,
    _site_dork_search,
)


class MockSubject:
    def __init__(self, name="Test Persoon", id="subj-1", email=None, phone=None):
        self.name = name
        self.id = id
        self.email = email
        self.phone = phone


class MockQuery:
    def __init__(self, items):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None

    def count(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


class MockCase:
    def __init__(self, subjects=None):
        self.subjects = MockQuery(subjects or [MockSubject()])


class MockAction:
    def __init__(self, data_value=None, case=None):
        self.data_value = data_value
        self.case = case or MockCase()


class MockCurlResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def make_mock_session(response):
    session = MagicMock()
    session.get.return_value = response
    return session


DORK_RESULT = {
    "source_url": "https://www.facebook.com/jan.vandijk",
    "title": "Jan van Dijk - Facebook",
    "detail": "Jan van Dijk uit Amsterdam",
    "source_type": "facebook",
    "icon": "📘",
}

facebook_pull_profile = {
    "data": {
        "name": "Jan van Dijk",
        "profile_url": "https://www.facebook.com/jan.vandijk",
        "location": "Amsterdam, Netherlands",
        "follower_count": 543,
        "bio": "Ondernemer en tech-liefhebber",
        "is_verified": True,
    }
}

instagram_pull_profile = {
    "status": "ok",
    "user": {
        "full_name": "Jan van Dijk",
        "username": "janvandijk",
        "biography": "Digital creator",
        "follower_count": 1234,
        "following_count": 567,
        "is_verified": False,
    },
}

linkedin_profile = {
    "full_name": "Jan van Dijk",
    "profile_url": "https://linkedin.com/in/janvandijk",
    "headline": "Software Engineer at ACME",
    "location": "Amsterdam",
    "summary": "Passionate builder",
    "follower_count": 890,
    "connection_count": 345,
}

twitter_profile = {
    "name": "Jan van Dijk",
    "screen_name": "janvandijk",
    "description": "Tweets over tech",
    "location": "Amsterdam",
    "followers_count": 2345,
    "friends_count": 789,
    "verified": True,
}

tiktok_profile = {
    "user": {
        "nickname": "Jan van Dijk",
        "unique_id": "janvandijk",
        "signature": "Content creator",
        "follower_count": 6789,
        "following_count": 234,
        "verification_type": 1,
    }
}


_THIS_MONTH = datetime.now().strftime("%Y-%m")


class TestCreditTracking:
    @patch("cms.models.Setting.get")
    def test_get_remaining_credits_full(self, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 10}}
        remaining = get_remaining_credits("tiktok")
        assert remaining == 40

    @patch("cms.models.Setting.get")
    def test_get_remaining_credits_exhausted(self, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 50}}
        remaining = get_remaining_credits("tiktok")
        assert remaining == 0

    @patch("cms.models.Setting.get")
    def test_get_remaining_credits_no_usage(self, mock_get):
        mock_get.return_value = {}
        remaining = get_remaining_credits("tiktok")
        assert remaining == 50

    @patch("cms.models.Setting.get")
    def test_get_remaining_credits_unknown_type(self, mock_get):
        mock_get.return_value = {}
        remaining = get_remaining_credits("unknown_type")
        assert remaining == 0

    @patch("cms.models.Setting.get")
    @patch("cms.models.Setting.set")
    def test_use_credit_increments(self, mock_set, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 5}}
        _use_credit("tiktok")
        args, _ = mock_set.call_args
        assert args[0] == "rapidapi_credits_usage"
        saved = json.loads(args[1])
        assert saved["tiktok"][_THIS_MONTH] == 6

    @patch("cms.models.Setting.get")
    def test_has_credits_true(self, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 10}}
        assert _has_credits("tiktok") is True

    @patch("cms.models.Setting.get")
    def test_has_credits_false(self, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 50}}
        assert _has_credits("tiktok") is False


# ─── _site_dork_search ────────────────────────────────────────


class TestSiteDorkSearch:
    @patch("cms.services.search_service.ddg_single_query", return_value=[])
    @patch("cms.services.search_service.brave_search")
    @patch("cms.workflow.actions.helpers._get_api_key", return_value="brave_key")
    def test_brave_success(self, mock_key, mock_brave, mock_ddg):
        mock_brave.return_value = [
            {
                "url": "https://www.facebook.com/jandevries",
                "title": "Jan de Vries",
                "description": "Profiel",
            }
        ]
        findings = _site_dork_search("facebook.com", "Jan de Vries")
        assert len(findings) == 1
        assert "facebook.com" in findings[0]["source_url"]
        assert findings[0]["source_type"] == "facebook"
        mock_brave.assert_called_once()
        mock_ddg.assert_not_called()

    @patch("cms.services.search_service.ddg_single_query")
    @patch("cms.services.search_service.brave_search", return_value=[])
    @patch("cms.workflow.actions.helpers._get_api_key", return_value="brave_key")
    def test_ddg_fallback(self, mock_key, mock_brave, mock_ddg):
        mock_ddg.return_value = [
            {
                "url": "https://www.instagram.com/jandevries",
                "title": "Jan de Vries",
                "description": "",
            }
        ]
        findings = _site_dork_search("instagram.com", "Jan de Vries")
        assert len(findings) == 1
        assert "instagram.com" in findings[0]["source_url"]
        mock_ddg.assert_called_once()

    @patch("cms.services.search_service.ddg_single_query", return_value=[])
    @patch("cms.services.search_service.brave_search", return_value=[])
    @patch("cms.workflow.actions.helpers._get_api_key", return_value=None)
    def test_no_keys_returns_empty(self, mock_key, mock_brave, mock_ddg):
        findings = _site_dork_search("facebook.com", "Jan de Vries")
        assert findings == []

    @patch("cms.services.search_service.ddg_single_query", return_value=[])
    @patch("cms.services.search_service.brave_search")
    @patch("cms.workflow.actions.helpers._get_api_key", return_value="brave_key")
    def test_deduplicates_urls(self, mock_key, mock_brave, mock_ddg):
        mock_brave.return_value = [
            {"url": "https://www.facebook.com/jan", "title": "Jan", "description": ""},
            {
                "url": "https://www.facebook.com/jan",
                "title": "Jan dup",
                "description": "",
            },
        ]
        findings = _site_dork_search("facebook.com", "Jan")
        assert len(findings) == 1


# ─── Facebook ─────────────────────────────────────────────────


class TestFacebookCheck:
    @patch(
        "cms.workflow.actions.platform_action._get_api_key", return_value="test_key_123"
    )
    @patch("cms.workflow.actions.platform_action._site_dork_search", return_value=[])
    @patch("cms.workflow.actions.platform_action.jittered_get")
    def test_pullapi_success(self, mock_get, mock_dork, mock_get_key):
        mock_get.return_value = MockCurlResponse(200, facebook_pull_profile)
        findings = _facebook_check(MockAction(data_value="jan.vandijk"))
        api_findings = [f for f in findings if "Facebook:" in f.get("title", "")]
        assert len(api_findings) == 1
        assert "Jan van Dijk" in api_findings[0]["title"]
        assert api_findings[0]["source_type"] == "facebook"

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value=None)
    @patch("cms.workflow.actions.platform_action._site_dork_search")
    def test_no_api_key_returns_dork_results(self, mock_dork, mock_get_key):
        mock_dork.return_value = [DORK_RESULT]
        findings = _facebook_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) >= 1
        assert any("facebook.com" in f.get("source_url", "") for f in findings)

    @patch(
        "cms.workflow.actions.platform_action._get_api_key", return_value="test_key_123"
    )
    @patch("cms.workflow.actions.platform_action._site_dork_search")
    @patch("cms.models.Setting.get", return_value={_THIS_MONTH: 100})
    def test_no_credits_returns_dork_results(self, mock_credit, mock_dork, mock_key):
        mock_dork.return_value = [DORK_RESULT]
        findings = _facebook_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) >= 1
        assert any("facebook.com" in f.get("source_url", "") for f in findings)


# ─── TikTok ───────────────────────────────────────────────────


class TestTikTokCheck:
    @patch(
        "cms.workflow.actions.platform_action._get_api_key", return_value="test_key_123"
    )
    @patch("cms.workflow.actions.platform_action._site_dork_search", return_value=[])
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.actions.platform_action.jittered_get")
    def test_username_success(
        self, mock_get, mock_set, mock_credit_get, mock_dork, mock_key
    ):
        mock_get.return_value = MockCurlResponse(200, tiktok_profile)
        findings = _tiktok_check(MockAction(data_value="janvandijk"))
        api_findings = [f for f in findings if "TikTok:" in f.get("title", "")]
        assert len(api_findings) == 1
        assert "Jan van Dijk" in api_findings[0]["title"]
        assert api_findings[0]["source_type"] == "tiktok"

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value=None)
    @patch("cms.workflow.actions.platform_action._site_dork_search")
    def test_no_api_key_returns_dork_results(self, mock_dork, mock_get_key):
        mock_dork.return_value = [
            {
                "source_url": "https://www.tiktok.com/@jandevries",
                "title": "Jan",
                "detail": "",
            }
        ]
        findings = _tiktok_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) >= 1
        assert any("tiktok.com" in f.get("source_url", "") for f in findings)


# ─── Instagram ────────────────────────────────────────────────


class TestInstagramCheck:
    @patch(
        "cms.workflow.actions.platform_action._get_api_key", return_value="test_key_123"
    )
    @patch("cms.workflow.actions.platform_action._site_dork_search", return_value=[])
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.actions.platform_action.jittered_get")
    def test_username_success(
        self, mock_get, mock_set, mock_credit_get, mock_dork, mock_key
    ):
        mock_get.return_value = MockCurlResponse(200, instagram_pull_profile)
        findings = _instagram_check(MockAction(data_value="janvandijk"))
        api_findings = [f for f in findings if "Instagram:" in f.get("title", "")]
        assert len(api_findings) == 1
        assert "Jan van Dijk" in api_findings[0]["title"]
        assert api_findings[0]["source_type"] == "instagram"

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value=None)
    @patch("cms.workflow.actions.platform_action._site_dork_search")
    def test_no_api_key_returns_dork_results(self, mock_dork, mock_get_key):
        mock_dork.return_value = [
            {
                "source_url": "https://www.instagram.com/jandevries",
                "title": "Jan",
                "detail": "",
            }
        ]
        findings = _instagram_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) >= 1
        assert any("instagram.com" in f.get("source_url", "") for f in findings)


# ─── LinkedIn ─────────────────────────────────────────────────


class TestLinkedInCheck:
    @patch(
        "cms.workflow.actions.platform_action._get_api_key", return_value="test_key_123"
    )
    @patch("cms.workflow.actions.platform_action._site_dork_search", return_value=[])
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.actions.platform_action.jittered_get")
    def test_url_success(
        self, mock_get, mock_set, mock_credit_get, mock_dork, mock_key
    ):
        mock_get.return_value = MockCurlResponse(200, {"data": linkedin_profile})
        findings = _linkedin_check(
            MockAction(data_value="https://linkedin.com/in/janvandijk")
        )
        api_findings = [f for f in findings if "LinkedIn:" in f.get("title", "")]
        assert len(api_findings) == 1, (
            f"Expected 1 API finding, got {len(api_findings)}: {api_findings}"
        )
        assert "Jan van Dijk" in api_findings[0]["title"]
        assert api_findings[0]["source_type"] == "linkedin"

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value=None)
    @patch("cms.workflow.actions.platform_action._site_dork_search")
    def test_no_api_key_returns_dork_results(self, mock_dork, mock_get_key):
        mock_dork.return_value = [
            {
                "source_url": "https://www.linkedin.com/in/jandevries",
                "title": "Jan",
                "detail": "",
            }
        ]
        findings = _linkedin_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) >= 1
        assert any("linkedin.com" in f.get("source_url", "") for f in findings)


# ─── Twitter ──────────────────────────────────────────────────


class TestTwitterCheck:
    @patch(
        "cms.workflow.actions.platform_action._get_api_key", return_value="test_key_123"
    )
    @patch("cms.workflow.actions.platform_action._site_dork_search", return_value=[])
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.actions.platform_action.jittered_get")
    def test_username_success(
        self, mock_get, mock_set, mock_credit_get, mock_dork, mock_key
    ):
        mock_get.return_value = MockCurlResponse(200, twitter_profile)
        findings = _twitter_check(MockAction(data_value="janvandijk"))
        api_findings = [f for f in findings if "Twitter:" in f.get("title", "")]
        assert len(api_findings) == 1
        assert "Jan van Dijk" in api_findings[0]["title"]
        assert api_findings[0]["source_type"] == "twitter"

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value=None)
    @patch("cms.workflow.actions.platform_action._site_dork_search")
    def test_no_api_key_returns_dork_results(self, mock_dork, mock_get_key):
        mock_dork.return_value = [
            {"source_url": "https://x.com/jandevries", "title": "Jan", "detail": ""},
        ]
        findings = _twitter_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) >= 1
        assert any(
            "twitter.com" in f.get("source_url", "")
            or "x.com" in f.get("source_url", "")
            for f in findings
        )
