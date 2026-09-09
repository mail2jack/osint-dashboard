"""
ADR-0005 PR-B: service/API wiring for research_actions.investigation_id.

The DB invariant (PR-A) is schema-level; these tests cover the service layer:
- create paths (run-action, create-proposals, subject profile run) accept an
  optional ``investigation_id`` when it is an open investigation of exactly
  the same case AND tenant, and stay case-wide (NULL) by default;
- cross-case, cross-tenant and archived investigations are refused with a
  clean 400/404 (never a 500 IntegrityError);
- link/unlink endpoints validate both the action and the investigation against
  the route case and tenant, are idempotent, and audit every deliberate scope
  change (ADR-0005 D3) with old and new values;
- archiving an investigation keeps existing links (history is kept).
"""

import uuid
from datetime import datetime, timezone

import pytest

from cms.models import (
    AuditLog,
    Investigation,
    InvestigationStatus,
    ResearchAction,
    db,
    User,
)
from cms.workflow.models import WorkflowCase


@pytest.fixture
def admin_tenant_id(app):
    return User.query.filter_by(username="admin").first().tenant_id


def _mk_case(admin_tenant_id, tag="PB") -> WorkflowCase:
    from cms.models import Client

    client = Client(
        tenant_id=admin_tenant_id,
        name=f"PB Client {tag} {uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = WorkflowCase(
        tenant_id=admin_tenant_id,
        case_number=f"PB-{tag}-{uuid.uuid4().hex[:8]}",
        client_id=client.id,
        title=f"PB case {tag} {uuid.uuid4().hex[:6]}",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    return case


def _mk_investigation(case, seq=1, archived=False):
    inv = Investigation(
        id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.id,
        sequence_no=seq,
        title="PB linked investigation",
    )
    if archived:
        inv.status = InvestigationStatus.ARCHIVED.value
        inv.archived_at = datetime.now(timezone.utc)
    db.session.add(inv)
    db.session.flush()
    return inv


def _mk_case_wide_action(case, action_type="google_dork"):
    action = ResearchAction(
        id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.id,
        action_type=action_type,
        data_value="site:example.nl",
        label="Dork",
        status="pending",
    )
    db.session.add(action)
    db.session.commit()
    return action


def _latest_action_audit(action_id):
    return (
        AuditLog.query.filter_by(entity_type="research_action", entity_id=action_id)
        .order_by(AuditLog.id.desc())
        .first()
    )


class TestRunActionScope:
    def test_run_action_with_investigation_id_links_and_audits(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "X",
                "investigation_id": inv.id,
            },
        )
        assert resp.status_code == 200
        action = db.session.get(ResearchAction, resp.get_json()["id"])
        assert action.investigation_id == inv.id
        audit = _latest_action_audit(action.id)
        assert audit is not None
        assert audit.action == "create"
        assert audit.new_values["investigation_id"] == inv.id
        assert inv.id in (audit.description or "")

    def test_run_action_case_wide_by_default(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={"action_type": "osint", "data_value": "X"},
        )
        assert resp.status_code == 200
        action = db.session.get(ResearchAction, resp.get_json()["id"])
        assert action.investigation_id is None
        audit = _latest_action_audit(action.id)
        assert audit is not None
        assert audit.new_values["investigation_id"] is None
        assert "case-wide" in (audit.description or "")

    def test_run_action_cross_case_investigation_rejected(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        other_case = _mk_case(admin_tenant_id, tag="OTHER")
        inv_other = _mk_investigation(other_case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "X",
                "investigation_id": inv_other.id,
            },
        )
        assert resp.status_code == 400
        assert "does not belong" in resp.get_json()["error"]

    def test_run_action_archived_investigation_rejected(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case, archived=True)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "X",
                "investigation_id": inv.id,
            },
        )
        assert resp.status_code == 400
        assert "open" in resp.get_json()["error"]


class TestCreateProposalsScope:
    def test_proposals_apply_investigation_scope(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={
                "action_types": ["osint", "google_dork"],
                "investigation_id": inv.id,
            },
        )
        assert resp.status_code == 200
        for action_id in resp.get_json()["ids"]:
            action = db.session.get(ResearchAction, action_id)
            assert action.investigation_id == inv.id
        audit = (
            AuditLog.query.filter_by(entity_type="research_action")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.new_values["investigation_id"] == inv.id

    def test_proposals_cross_case_investigation_rejected(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        other_case = _mk_case(admin_tenant_id, tag="OTHER")
        inv_other = _mk_investigation(other_case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={
                "action_types": ["osint", "google_dork"],
                "investigation_id": inv_other.id,
            },
        )
        assert resp.status_code == 400


class TestLinkUnlinkEndpoints:
    def test_link_open_investigation(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        action = _mk_case_wide_action(case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link",
            json={"investigation_id": inv.id},
        )
        assert resp.status_code == 200
        assert db.session.get(ResearchAction, action.id).investigation_id == inv.id
        audit = _latest_action_audit(action.id)
        assert audit.action == "link"
        assert audit.old_values["investigation_id"] is None
        assert audit.new_values["investigation_id"] == inv.id

    def test_link_is_idempotent(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        action = _mk_case_wide_action(case)
        db.session.commit()
        action.investigation_id = inv.id
        db.session.commit()
        before = AuditLog.query.filter_by(
            entity_id=action.id, entity_type="research_action"
        ).count()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link",
            json={"investigation_id": inv.id},
        )
        assert resp.status_code == 200
        assert (
            AuditLog.query.filter_by(
                entity_id=action.id, entity_type="research_action"
            ).count()
            == before
        )

    def test_unlink_back_to_case_wide(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        action = _mk_case_wide_action(case)
        action.investigation_id = inv.id
        db.session.commit()
        resp = auth_client.delete(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link"
        )
        assert resp.status_code == 200
        assert db.session.get(ResearchAction, action.id).investigation_id is None
        audit = _latest_action_audit(action.id)
        assert audit.action == "unlink"
        assert audit.old_values["investigation_id"] == inv.id
        assert audit.new_values["investigation_id"] is None

    def test_link_cross_case_investigation_rejected(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        other_case = _mk_case(admin_tenant_id, tag="OTHER")
        inv_other = _mk_investigation(other_case)
        action = _mk_case_wide_action(case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link",
            json={"investigation_id": inv_other.id},
        )
        assert resp.status_code == 400
        assert db.session.get(ResearchAction, action.id).investigation_id is None

    def test_link_archived_investigation_rejected(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case, archived=True)
        action = _mk_case_wide_action(case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link",
            json={"investigation_id": inv.id},
        )
        assert resp.status_code == 400

    def test_link_unknown_investigation_rejected(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        action = _mk_case_wide_action(case)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link",
            json={"investigation_id": "does-not-exist"},
        )
        assert resp.status_code == 404

    def test_link_wrong_case_action_404(self, auth_client, admin_tenant_id):
        case = _mk_case(admin_tenant_id)
        other_case = _mk_case(admin_tenant_id, tag="OTHER")
        inv = _mk_investigation(other_case)
        action = _mk_case_wide_action(other_case, action_type="osint")
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/link",
            json={"investigation_id": inv.id},
        )
        assert resp.status_code == 404

    def test_archiving_investigation_keeps_existing_links(
        self, auth_client, admin_tenant_id
    ):
        case = _mk_case(admin_tenant_id)
        inv = _mk_investigation(case)
        action = _mk_case_wide_action(case)
        action.investigation_id = inv.id
        db.session.commit()

        inv.status = InvestigationStatus.ARCHIVED.value
        inv.archived_at = datetime.now(timezone.utc)
        db.session.commit()
        assert db.session.get(ResearchAction, action.id).investigation_id == inv.id

        fresh = _mk_case_wide_action(case, action_type="osint")
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{fresh.id}/link",
            json={"investigation_id": inv.id},
        )
        assert resp.status_code == 400