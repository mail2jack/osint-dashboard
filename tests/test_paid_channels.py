"""
ADR-0001 PR6 (D1.6) — Dork-first source policy: paid channels off by default
behind explicit tenant config (FeatureFlag), cost labels, composed browser
query as a proposal, and Facebook credit metering.
"""

import json
import time
from datetime import datetime
from unittest.mock import patch

from cms.models import (
    Case,
    FeatureFlag,
    Finding,
    ResearchAction,
    User,
    db,
)
from cms.workflow.actions.registry import (
    ACTION_REGISTRY,
    CREDIT_LIMITS,
    get_remaining_credits,
    is_paid_action,
    paid_channels_enabled,
)
from cms.workflow.actions.platform_action import _facebook_check
from cms.workflow.actions.osint_action import _compose_browser_urls

_THIS_MONTH = datetime.now().strftime("%Y-%m")


def _admin(app):
    return User.query.filter_by(username="admin").first()


def _create_case_with_subject(auth_client, title="PR6 Subject Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "PR6 Client",
            "title": title,
            "subject_0_name": "PR6 Person",
            "subject_0_type": "person",
            "subject_0_email": "pr6@example.com",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    case = Case.query.filter_by(title=title).first()
    assert case is not None
    return case


def _enable_paid_channels(tenant_id):
    flag = FeatureFlag(tenant_id=tenant_id, flag_name="paid_channels", enabled=True)
    db.session.add(flag)
    db.session.commit()
    return flag


class TestPaidChannelsFeatureFlag:
    def test_default_off_without_override(self, app):
        admin = _admin(app)
        assert paid_channels_enabled(admin.tenant_id) is False
        assert (
            FeatureFlag.query.filter_by(
                tenant_id=admin.tenant_id, flag_name="paid_channels"
            ).first()
            is None
        )

    def test_enabled_via_override(self, app):
        admin = _admin(app)
        _enable_paid_channels(admin.tenant_id)
        assert paid_channels_enabled(admin.tenant_id) is True

    def test_unknown_tenant_off(self, app):
        assert paid_channels_enabled("no-such-tenant") is False


class TestPaidChannelGate:
    def test_run_paid_blocked_when_disabled(self, auth_client):
        case = _create_case_with_subject(auth_client)
        for mode in ("run", "proposal"):
            resp = auth_client.post(
                f"/cms/workflow/api/case/{case.id}/run-action",
                json={
                    "action_type": "facebook",
                    "data_value": "PR6 Person",
                    "mode": mode,
                },
            )
            assert resp.status_code == 409, mode
            assert "disabled" in resp.get_json()["error"].lower()

    def test_open_actions_not_blocked(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "PR6 Person",
                "mode": "proposal",
            },
        )
        assert resp.status_code == 200

    def test_paid_allowed_when_enabled(self, auth_client, app):
        case = _create_case_with_subject(auth_client)
        _enable_paid_channels(_admin(app).tenant_id)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "facebook",
                "data_value": "PR6 Person",
                "mode": "proposal",
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "proposal"

    def test_start_paid_proposal_blocked_when_disabled(self, auth_client, app):
        case = _create_case_with_subject(auth_client)
        action = ResearchAction(
            case_id=case.id,
            action_type="facebook",
            target_kind="case",
            data_value="PR6 Person",
            label="Facebook research",
            status="proposal",
            tenant_id=_admin(app).tenant_id,
        )
        db.session.add(action)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/start"
        )
        assert resp.status_code == 409
        assert db.session.get(ResearchAction, action.id).status == "proposal"

    @patch("cms.workflow.actions.registry.run_action", return_value=None)
    def test_start_paid_proposal_allowed_when_enabled(self, mock_run, auth_client, app):
        case = _create_case_with_subject(auth_client)
        _enable_paid_channels(_admin(app).tenant_id)
        action = ResearchAction(
            case_id=case.id,
            action_type="facebook",
            target_kind="case",
            data_value="PR6 Person",
            label="Facebook research",
            status="proposal",
            tenant_id=_admin(app).tenant_id,
        )
        db.session.add(action)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/start"
        )
        assert resp.status_code == 200
        time.sleep(0.3)  # let the no-op worker thread run
        assert db.session.get(ResearchAction, action.id).status == "pending"


class TestBulkProposalsFilter:
    def test_paid_filtered_out_of_bulk(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={
                "action_types": ["email", "facebook", "tiktok", "osint"],
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["ids"]) == 2
        assert body["skipped"] == ["facebook", "tiktok"]
        created_types = [
            db.session.get(ResearchAction, aid).action_type for aid in body["ids"]
        ]
        assert "facebook" not in created_types
        assert "tiktok" not in created_types

    def test_all_paid_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={"action_types": ["facebook"]},
        )
        assert resp.status_code == 422
        body = resp.get_json()
        assert body["skipped"] == ["facebook"]
        assert "never auto-proposed" in body["error"]


class TestCostLabel:
    def test_paid_channels_have_cost_label(self):
        for key in ("facebook", "instagram", "tiktok", "linkedin", "twitter"):
            assert is_paid_action(key)
            assert ACTION_REGISTRY[key]["cost_label"], key

    def test_browser_search_is_open_without_cost(self):
        assert ACTION_REGISTRY["browser_search"]["category"] == "open"
        assert ACTION_REGISTRY["browser_search"]["cost_label"] == ""

    def test_case_detail_shows_locked_paid_cards(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'data-paid-locked="1"' in html
        assert "~€" in html  # cost label rendered


class TestBrowserSearch:
    def test_compose_browser_urls(self):
        urls = _compose_browser_urls('"Jan van Dijk" site:facebook.com')
        assert set(urls) == {"google", "bing", "duckduckgo"}
        assert "https://www.google.com/search?q=" in urls["google"]
        assert "https://www.bing.com/search?q=" in urls["bing"]
        assert "https://duckduckgo.com/?q=" in urls["duckduckgo"]
        assert "%22Jan%20van%20Dijk%22" in urls["google"]

    def test_empty_query_composes_nothing(self):
        assert _compose_browser_urls("") == {}
        assert _compose_browser_urls("   ") == {}

    def test_browser_search_flow(self, auth_client):
        case = _create_case_with_subject(auth_client)
        payload = json.dumps(
            {
                "dork_id": "personal-name",
                "dork_label": "Full name search",
                "query": '"PR6 Person" site:facebook.com',
                "variables": {"name": "PR6 Person"},
            }
        )
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "browser_search",
                "data_value": payload,
                "mode": "proposal",
            },
        )
        assert resp.status_code == 200
        aid = resp.get_json()["id"]
        assert db.session.get(ResearchAction, aid).status == "proposal"

        start = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{aid}/start"
        )
        assert start.status_code == 200

        action = None
        for _ in range(50):
            db.session.expire_all()
            action = db.session.get(ResearchAction, aid)
            if action.status in ("completed", "error"):
                break
            time.sleep(0.05)
        assert action.status == "completed", action.error

        findings = Finding.query.filter_by(
            case_id=case.id, source_type="browser_search"
        ).all()
        assert len(findings) == 3
        urls = [f.source_url for f in findings]
        assert any("google.com/search" in u for u in urls)
        assert any("bing.com/search" in u for u in urls)
        assert any("duckduckgo.com" in u for u in urls)
        assert all("facebook.com" in u for u in urls)

    def test_no_queries_at_subject_creation(self, auth_client):
        """Creating a case/subject must not start or propose any queries."""
        case = _create_case_with_subject(auth_client)
        assert ResearchAction.query.filter_by(case_id=case.id).count() == 0


class MockCurlResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class MockAction:
    def __init__(self, data_value):
        self.data_value = data_value
        self.subject_id = None
        self.case = None


class TestFacebookCreditMetering:
    def test_facebook_credit_limit_registered(self):
        assert CREDIT_LIMITS["facebook"] == 30

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value="test_key")
    @patch("cms.workflow.actions.platform_action._site_dork_search", return_value=[])
    @patch(
        "cms.workflow.actions.platform_action.jittered_get",
        return_value=MockCurlResponse(
            200,
            {
                "data": {
                    "name": "Jan van Dijk",
                    "profile_url": "https://www.facebook.com/jan.vandijk",
                }
            },
        ),
    )
    def test_api_result_consumes_credit(self, mock_get, mock_dork, mock_key):
        before = get_remaining_credits("facebook")
        findings = _facebook_check(MockAction(data_value="jan.vandijk"))
        assert len(findings) == 1
        assert get_remaining_credits("facebook") == before - 1

    @patch("cms.workflow.actions.platform_action._get_api_key", return_value="test_key")
    @patch(
        "cms.workflow.actions.platform_action._site_dork_search",
        return_value=[
            {
                "source_url": "https://www.facebook.com/jan.vandijk",
                "title": "Jan van Dijk - Facebook",
                "detail": "",
                "source_type": "facebook",
                "icon": "📘",
            }
        ],
    )
    @patch("cms.models.Setting.get", return_value={"facebook": {_THIS_MONTH: 30}})
    def test_no_credits_returns_dork_only(self, mock_credit, mock_dork, mock_key):
        findings = _facebook_check(MockAction(data_value="Jan van Dijk"))
        assert len(findings) == 1
        assert "facebook.com" in findings[0]["source_url"]
        assert get_remaining_credits("facebook") == 0
