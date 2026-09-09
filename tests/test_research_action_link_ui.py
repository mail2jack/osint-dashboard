"""
ADR-0005 PR-C: UI wiring for linking research actions to an investigation.

The write-side invariant (PR-B) is API-tested in
``test_research_action_link_api.py``. These tests cover what the UI actually
renders and posts, all read-only at the template level:

- ``case_status`` returns per-action investigation scope fields that the
  polling JS uses for badges/filtering;
- ``case_detail`` embeds ``investigations_meta`` (open + archived, so
  historical links keep their badge), a server-rendered scope filter and per
  group a badge + link/unlink ``<select>``;
- the subject profile embeds ``investigation_options`` (only OPEN,
  non-archived) and the Propose / Quick Start pickers post ``investigation_id``
  through the API;
- the profile action table shows a scope badge column.
"""

import json
import re
import uuid
from datetime import datetime, timezone

import pytest

from cms.models import (
    ActionFinding,
    FeatureFlag,
    Investigation,
    InvestigationStatus,
    ResearchAction,
    Subject,
    User,
    db,
)
from cms.workflow.models import WorkflowCase, WorkflowFinding

INVESTIGATION_PAYLOAD_KEYS = (
    "investigation_id",
    "investigation_number",
    "investigation_title",
)


@pytest.fixture
def admin_tenant_id(app):
    return User.query.filter_by(username="admin").first().tenant_id


def _mk_case(admin_tenant_id, tag="UI", subject=None) -> WorkflowCase:
    from cms.models import Client

    client = Client(
        tenant_id=admin_tenant_id,
        name=f"UI Client {tag} {uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = WorkflowCase(
        tenant_id=admin_tenant_id,
        case_number=f"UI-{tag}-{uuid.uuid4().hex[:8]}",
        client_id=client.id,
        title=f"UI case {tag} {uuid.uuid4().hex[:6]}",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    if subject is not None:
        case.subjects.append(subject)
    return case


def _mk_subject(admin_tenant_id):
    subject = Subject(
        tenant_id=admin_tenant_id,
        name="UI Subject",
        subject_type="person",
    )
    db.session.add(subject)
    db.session.flush()
    return subject


def _mk_investigation(case, seq=1, archived=False):
    inv = Investigation(
        id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.id,
        sequence_no=seq,
        title="UI linked investigation",
    )
    if archived:
        inv.status = InvestigationStatus.ARCHIVED.value
        inv.archived_at = datetime.now(timezone.utc)
    db.session.add(inv)
    db.session.flush()
    return inv


def _mk_action(case, action_type="google_dork", investigation_id=None, subject_id=None):
    action = ResearchAction(
        id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.id,
        action_type=action_type,
        data_value="site:example.nl",
        label="Dork",
        status="pending",
        investigation_id=investigation_id,
        subject_id=subject_id,
    )
    db.session.add(action)
    db.session.flush()
    return action


def _mk_finding(case, action, user_id):
    finding = WorkflowFinding(
        tenant_id=case.tenant_id,
        case_id=case.id,
        title="UI finding",
        content="UI content",
        created_by=user_id,
    )
    db.session.add(finding)
    db.session.flush()
    db.session.add(
        ActionFinding(action_id=action.id, finding_id=finding.id)
    )
    db.session.flush()
    return finding


def _enable_flag(tenant_id):
    db.session.add(
        FeatureFlag(
            tenant_id=tenant_id,
            flag_name="subject_first_investigations",
            enabled=True,
        )
    )
    db.session.commit()


def _embed_json(html, var_name):
    m = re.search(
        re.escape("const " + var_name) + r"\s*=\s*(\{.*?\});",
        html,
        re.DOTALL,
    )
    return json.loads(m.group(1))


class TestCaseStatusScopePayload:
    def test_action_payload_carries_investigation_fields(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        linked = _mk_action(case, investigation_id=inv.id)
        _mk_action(case)  # case-wide
        db.session.commit()

        resp = auth_client.get(f"/cms/workflow/api/case/{case.id}/status")
        assert resp.status_code == 200
        actions = resp.get_json()["actions"]
        payload = next(a for a in actions if a["id"] == linked.id)
        for key in INVESTIGATION_PAYLOAD_KEYS:
            assert key in payload
        assert payload["investigation_id"] == inv.id
        assert payload["investigation_number"] == inv.human_number
        assert payload["investigation_title"] == inv.title

    def test_case_wide_action_payload_has_null_scope(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        action = _mk_action(case)
        db.session.commit()
        resp = auth_client.get(f"/cms/workflow/api/case/{case.id}/status")
        payload = next(
            a for a in resp.get_json()["actions"] if a["id"] == action.id
        )
        for key in INVESTIGATION_PAYLOAD_KEYS:
            assert key in payload
        assert payload["investigation_id"] is None


class TestCaseDetailScopeUi:
    def test_embeds_investigations_meta_including_archived(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        open_inv = _mk_investigation(case, seq=1)
        archived_inv = _mk_investigation(case, seq=2, archived=True)
        db.session.commit()

        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Scope filter select present with both open and (labeled) archived options.
        assert 'id="scopeFilter"' in html
        assert f'value="{open_inv.id}"' in html
        assert f'value="{archived_inv.id}"' in html
        assert "(gearchiveerd)" in html

        # JS config exposes all investigations.
        assert f'"id": "{open_inv.id}"' in html
        assert '"archived": true' in html

    def test_server_rendered_badge_and_link_select(
        self, auth_client, admin_tenant_id
    ):
        admin = User.query.filter_by(username="admin").first()
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        linked = _mk_action(case, investigation_id=inv.id)
        wide = _mk_action(case, action_type="osint")
        db.session.commit()
        _mk_finding(case, linked, admin.id)
        _mk_finding(case, wide, admin.id)
        db.session.commit()

        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        html = resp.get_data(as_text=True)

        # Badged with the investigation's human number; case-wide stays Zaakbreed.
        assert f">🔗 {inv.human_number}<" in html
        assert "🌐 Zaakbreed" in html
        # The per-group link/unlink selector is server-rendered too.
        assert 'class="scope-link"' in html

    def test_historical_archived_link_keeps_badge_and_select_option(
        self, auth_client, admin_tenant_id
    ):
        admin = User.query.filter_by(username="admin").first()
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case, archived=True)
        linked = _mk_action(case, investigation_id=inv.id)
        db.session.commit()
        _mk_finding(case, linked, admin.id)
        db.session.commit()

        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        html = resp.get_data(as_text=True)

        # Historical link survives: badge shows the number, select keeps it as
        # the current (archived-flagged) option.
        assert f">🔗 {inv.human_number}<" in html
        assert f'value="{inv.id}" selected' in html
        assert "(gearchiveerd)" in html


class TestSubjectProfileScopeUi:
    def test_investigation_options_only_open_not_archived(
        self, auth_client, admin_tenant_id
    ):
        _enable_flag(admin_tenant_id)
        subject = _mk_subject(admin_tenant_id)
        case = _mk_case(admin_tenant_id, subject=subject)
        open_inv = _mk_investigation(case, seq=1)
        _mk_investigation(case, seq=2, archived=True)
        db.session.commit()

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        options = _embed_json(html, "INVESTIGATION_OPTIONS")
        per_case = options.get(case.id) or options.get(str(case.id))
        assert per_case is not None
        assert [i["id"] for i in per_case] == [open_inv.id]
        assert per_case[0]["human_number"] == open_inv.human_number

        # Both pickers render the open investigation as an option.
        assert 'id="propose-investigation-id"' in html
        assert 'id="action-investigation-id"' in html

    def test_action_rows_show_scope_badge(
        self, auth_client, admin_tenant_id
    ):
        _enable_flag(admin_tenant_id)
        subject = _mk_subject(admin_tenant_id)
        case = _mk_case(admin_tenant_id, subject=subject)
        inv = _mk_investigation(case)
        _mk_action(case, investigation_id=inv.id, subject_id=subject.id)
        _mk_action(case, action_type="osint", subject_id=subject.id)  # case-wide
        db.session.commit()

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        html = resp.get_data(as_text=True)

        assert f">🔗 {inv.human_number}<" in html
        assert "🌐 Zaakbreed" in html

    def test_run_action_scopes_to_investigation(
        self, auth_client, admin_tenant_id
    ):
        _enable_flag(admin_tenant_id)
        subject = _mk_subject(admin_tenant_id)
        case = _mk_case(admin_tenant_id, subject=subject)
        inv = _mk_investigation(case)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/api/profile/subjects/{subject.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "X",
                "case_id": case.id,
                "investigation_id": inv.id,
            },
        )
        assert resp.status_code in (200, 201)
        action = db.session.get(ResearchAction, resp.get_json()["id"])
        assert action.investigation_id == inv.id

    def test_propose_form_posts_investigation_id(
        self, auth_client, admin_tenant_id
    ):
        _enable_flag(admin_tenant_id)
        subject = _mk_subject(admin_tenant_id)
        case = _mk_case(admin_tenant_id, subject=subject)
        inv = _mk_investigation(case)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={
                "action_types": ["osint"],
                "subject_id": subject.id,
                "investigation_id": inv.id,
            },
        )
        assert resp.status_code == 200
        action = db.session.get(ResearchAction, resp.get_json()["ids"][0])
        assert action.investigation_id == inv.id