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
)


class MockSubject:
    def __init__(self, name="Test Persoon", email=None, phone=None):
        self.name = name
        self.email = email
        self.phone = phone


class MockCase:
    def __init__(self, subjects=None):
        self.subjects = subjects or [MockSubject()]


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
    "data": {
        "full_name": "Jan van Dijk",
        "username": "janvandijk",
        "biography": "Digital creator",
        "follower_count": 1234,
        "following_count": 567,
        "is_verified": False,
    }
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
        assert args[1]["tiktok"][_THIS_MONTH] == 6

    @patch("cms.models.Setting.get")
    def test_has_credits_true(self, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 10}}
        assert _has_credits("tiktok") is True

    @patch("cms.models.Setting.get")
    def test_has_credits_false(self, mock_get):
        mock_get.return_value = {"tiktok": {_THIS_MONTH: 50}}
        assert _has_credits("tiktok") is False


class TestFacebookCheck:
    @patch("cms.workflow.research._get_api_key", return_value="test_key_123")
    @patch("cms.workflow.research.jittered_get")
    def test_pullapi_success(self, mock_get, mock_get_key):
        mock_get.return_value = MockCurlResponse(200, facebook_pull_profile)
        findings = _facebook_check(MockAction(data_value="jan.vandijk"))
        assert len(findings) == 1
        assert "Jan van Dijk" in findings[0]["title"]
        assert findings[0]["source_type"] == "facebook"

    @patch("cms.workflow.research._get_api_key", return_value="test_key_123")
    @patch("cms.workflow.research.jittered_get")
    def test_no_api_key(self, mock_get, mock_get_key):
        mock_get_key.return_value = None
        findings = _facebook_check(MockAction(data_value="jan.vandijk"))
        assert len(findings) == 1
        assert "niet beschikbaar" in findings[0]["title"].lower()


class TestTikTokCheck:
    @patch("cms.workflow.research._get_api_key", return_value="test_key_123")
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.research.jittered_get")
    def test_username_success(self, mock_get, mock_set, mock_credit_get, mock_key):
        mock_get.return_value = MockCurlResponse(200, tiktok_profile)
        findings = _tiktok_check(MockAction(data_value="janvandijk"))
        assert len(findings) == 1
        assert "Jan van Dijk" in findings[0]["title"]
        assert findings[0]["source_type"] == "tiktok"


class TestInstagramCheck:
    @patch("cms.workflow.research._get_api_key", return_value="test_key_123")
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.research.jittered_get")
    def test_username_success(self, mock_get, mock_set, mock_credit_get, mock_key):
        mock_get.return_value = MockCurlResponse(200, instagram_pull_profile)
        findings = _instagram_check(MockAction(data_value="janvandijk"))
        assert len(findings) == 1
        assert "Jan van Dijk" in findings[0]["title"]
        assert findings[0]["source_type"] == "instagram"


class TestLinkedInCheck:
    @patch("cms.workflow.research._get_api_key", return_value="test_key_123")
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.research.jittered_get")
    def test_url_success(self, mock_get, mock_set, mock_credit_get, mock_key):
        mock_get.return_value = MockCurlResponse(200, {"data": linkedin_profile})
        findings = _linkedin_check(
            MockAction(data_value="https://linkedin.com/in/janvandijk")
        )
        assert len(findings) == 1, (
            f"Expected 1 finding, got {len(findings)}: {findings}"
        )
        assert "Jan van Dijk" in findings[0]["title"]
        assert findings[0]["source_type"] == "linkedin"


class TestTwitterCheck:
    @patch("cms.workflow.research._get_api_key", return_value="test_key_123")
    @patch("cms.models.Setting.get", return_value={})
    @patch("cms.models.Setting.set")
    @patch("cms.workflow.research.jittered_get")
    def test_username_success(self, mock_get, mock_set, mock_credit_get, mock_key):
        mock_get.return_value = MockCurlResponse(200, twitter_profile)
        findings = _twitter_check(MockAction(data_value="janvandijk"))
        assert len(findings) == 1
        assert "Jan van Dijk" in findings[0]["title"]
        assert findings[0]["source_type"] == "twitter"
