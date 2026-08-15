"""
Research flow (ADR-0001 PR5, D1.5): findings verification lifecycle,
proposal-mode actions, bulk proposals, and action channel categories.
"""

from datetime import datetime, timezone

from cms.models import Case, Client, Finding, User, db, ResearchAction
from cms.workflow.actions.registry import (
    action_category,
    is_paid_action,
)


def _orm_case(title="PR5 Model Case"):
    client = Client(name="PR5 Model Client")
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"PR5-{title.replace(' ', '').upper()}",
        client_id=client.id,
        title=title,
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    return case


def _orm_finding(case, user, title="Draft finding", status=None):
    finding = Finding(
        case_id=case.id,
        tenant_id=case.tenant_id,
        created_by=user.id,
        title=title,
        content="evidence",
        source_type="osint",
    )
    if status == "verified":
        finding.promote_to_verified(user)
    elif status == "rejected":
        finding.reject(user)
    db.session.add(finding)
    db.session.commit()
    return finding


def _create_case_with_subject(auth_client, title="PR5 Subject Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "PR5 Client",
            "title": title,
            "subject_0_name": "PR5 Person",
            "subject_0_type": "person",
            "subject_0_email": "pr5@example.com",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    case = Case.query.filter_by(title=title).first()
    assert case is not None
    return case


class TestFindingLifecycle:
    def test_promote_sets_verified_state(self, app, db_session):
        case = _orm_case()
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        assert finding.status is None
        finding.promote_to_verified(admin)
        assert finding.status == "verified"
        assert finding.verified is True
        assert finding.verified_by == admin.id
        assert finding.verified_at is not None

    def test_demote_clears_verified_state(self, app, db_session):
        case = _orm_case()
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        finding.promote_to_verified(admin)
        finding.demote_to_candidate()
        assert finding.status == "candidate"
        assert finding.verified is False
        assert finding.verified_by is None
        assert finding.verified_at is None

    def test_reject_state(self, app, db_session):
        case = _orm_case()
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        finding.reject(admin)
        assert finding.status == "rejected"
        assert finding.verified is False
        assert finding.verified_by == admin.id

    def test_to_dict_includes_lifecycle(self, app, db_session):
        case = _orm_case()
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin, status="verified")
        d = finding.to_dict()
        assert d["status"] == "verified"
        assert d["verified"] is True
        assert d["verified_by"] == admin.id
        assert d["verified_at"] is not None
        assert d["verifier_name"] == admin.full_name

    def test_author_relationship_resolves(self, app, db_session):
        """The User.findings backref must still resolve after the second FK
        (verified_by) was added to Finding."""
        case = _orm_case()
        admin = User.query.filter_by(username="admin").first()
        _orm_finding(case, admin, title="Author-linked finding")
        db.session.commit()
        assert any(f.title == "Author-linked finding" for f in admin.findings)


class TestActionCategories:
    def test_paid_channels_classified(self):
        assert action_category("instagram") == "paid"
        assert action_category("tiktok") == "paid"
        assert action_category("linkedin") == "paid"
        assert action_category("twitter") == "paid"
        assert action_category("facebook") == "paid"
        assert is_paid_action("instagram")

    def test_local_actions_classified(self):
        assert action_category("photo_analysis") == "local"
        assert action_category("manual_entry") == "local"

    def test_open_actions_classified(self):
        assert action_category("email") == "open"
        assert action_category("phone") == "open"
        assert action_category("address") == "open"
        assert action_category("google_dork") == "open"
        assert action_category("osint") == "open"
        assert not is_paid_action("google_dork")

    def test_unknown_action_defaults_open(self):
        assert action_category("does_not_exist") == "open"


class TestProposalMode:
    def test_proposal_mode_creates_proposal(self, auth_client):
        case = _create_case_with_subject(auth_client)
        subject = case.subjects.first()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "PR5 Person",
                "subject_id": str(subject.id),
                "mode": "proposal",
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "proposal"
        action = db.session.get(ResearchAction, body["id"])
        assert action.status == "proposal"
        assert action.subject_id == subject.id
        assert action.target_kind == "subject"

    def test_proposal_mode_does_not_start(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={"action_type": "google_dork", "data_value": "x", "mode": "proposal"},
        )
        assert resp.status_code == 200
        action = db.session.get(ResearchAction, resp.get_json()["id"])
        assert action.status == "proposal"
        assert action.started_at is None

    def test_unknown_mode_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={"action_type": "osint", "data_value": "x", "mode": "nonsense"},
        )
        assert resp.status_code == 400

    def test_proposal_not_blocked_by_running_action(self, auth_client):
        """Proposals must not conflict with the running-action dedupe check."""
        case = _create_case_with_subject(auth_client)
        running = ResearchAction(
            case_id=case.id,
            action_type="osint",
            target_kind="case",
            data_value="x",
            label="OSINT",
            status="running",
        )
        db.session.add(running)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={"action_type": "osint", "data_value": "x", "mode": "proposal"},
        )
        assert resp.status_code == 200
        assert (
            db.session.get(ResearchAction, resp.get_json()["id"]).status == "proposal"
        )


class TestBulkProposals:
    def test_create_proposals(self, auth_client):
        case = _create_case_with_subject(auth_client)
        subject = case.subjects.first()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={"subject_id": str(subject.id), "action_types": ["email", "osint"]},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert len(body["ids"]) == 2
        for aid in body["ids"]:
            assert db.session.get(ResearchAction, aid).status == "proposal"

    def test_create_proposals_empty_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(f"/cms/workflow/api/case/{case.id}/proposals", json={})
        assert resp.status_code == 400

    def test_create_proposals_unknown_action(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={"action_types": ["not_an_action"]},
        )
        assert resp.status_code == 400

    def test_start_proposal_runs_action(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={"action_types": ["google_dork"]},
        )
        aid = resp.get_json()["ids"][0]
        start = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{aid}/start"
        )
        assert start.status_code == 200
        # The async worker may already have advanced past "pending" — the
        # contract is that the proposal is started (no longer a proposal).
        assert db.session.get(ResearchAction, aid).status != "proposal"

    def test_delete_proposal(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={"action_types": ["google_dork"]},
        )
        aid = resp.get_json()["ids"][0]
        deleted = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{aid}/delete"
        )
        assert deleted.status_code == 200
        assert db.session.get(ResearchAction, aid) is None

    def test_delete_non_proposal_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        action = ResearchAction(
            case_id=case.id,
            action_type="google_dork",
            target_kind="case",
            data_value="x",
            label="Dork",
            status="completed",
        )
        db.session.add(action)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/actions/{action.id}/delete"
        )
        assert resp.status_code == 400


class TestVerifyStatusUpgrade:
    def test_verify_promotes_finding(self, auth_client):
        case = _create_case_with_subject(auth_client)
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{finding.id}/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "verified"
        assert body["verified"] is True
        db.session.refresh(finding)
        assert finding.verified_by == admin.id

    def test_reject_sets_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{finding.id}/verify",
            json={"status": "rejected"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "rejected"
        db.session.refresh(finding)
        assert finding.verified is False

    def test_legacy_boolean_toggle_still_works(self, auth_client):
        case = _create_case_with_subject(auth_client)
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{finding.id}/verify",
            json={"verified": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "verified"

    def test_demote_via_candidate(self, auth_client):
        case = _create_case_with_subject(auth_client)
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin, status="verified")
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{finding.id}/verify",
            json={"status": "candidate"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "candidate"
        db.session.refresh(finding)
        assert finding.verified_by is None

    def test_invalid_status_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{finding.id}/verify",
            json={"status": "bogus"},
        )
        assert resp.status_code == 400

    def test_status_json_exposes_lifecycle(self, auth_client):
        case = _create_case_with_subject(auth_client)
        admin = User.query.filter_by(username="admin").first()
        finding = _orm_finding(case, admin, status="verified")
        db.session.commit()
        resp = auth_client.get(f"/cms/workflow/api/case/{case.id}/status")
        assert resp.status_code == 200
        found = next(f for f in resp.get_json()["findings"] if f["id"] == finding.id)
        assert found["status"] == "verified"
        assert found["verified_by"] == admin.id
        assert found["verifier_name"] == admin.full_name
        assert found["verified_at"] is not None

    def test_proposals_in_status_json(self, auth_client):
        case = _create_case_with_subject(auth_client)
        auth_client.post(
            f"/cms/workflow/api/case/{case.id}/proposals",
            json={"action_types": ["google_dork"]},
        )
        resp = auth_client.get(f"/cms/workflow/api/case/{case.id}/status")
        assert resp.status_code == 200
        proposals = [a for a in resp.get_json()["actions"] if a["status"] == "proposal"]
        assert len(proposals) == 1
        assert proposals[0]["category"] == "open"
