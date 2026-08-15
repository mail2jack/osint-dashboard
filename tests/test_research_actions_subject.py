"""
Subject-centric research actions (ADR-0001 PR4).

Covers the research_actions.subject_id / target_kind / target_snapshot columns,
the run-action API subject validation, and the subject-aware action resolution.
"""

import json
from datetime import datetime, timezone

from cms.models import Case, Client, db, ResearchAction, Subject
from cms.workflow.actions.helpers import (
    SUBJECT_TYPE_PRESETS,
    _action_subject,
    presets_for_subject,
)


def _orm_case(title="PR4 Model Case"):
    client = Client(name="PR4 Model Client")
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"PR4-{title.replace(' ', '').upper()}",
        client_id=client.id,
        title=title,
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    return case


def _create_case_with_subject(auth_client, title="PR4 Subject Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "PR4 Client",
            "title": title,
            "subject_0_name": "PR4 Person",
            "subject_0_type": "person",
            "subject_0_email": "pr4@example.com",
            "subject_0_phone": "+31612345678",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    case = Case.query.filter_by(title=title).first()
    assert case is not None
    return case


class TestModelColumns:
    def test_subject_columns_exist(self, app):
        assert hasattr(ResearchAction, "subject_id")
        assert hasattr(ResearchAction, "target_kind")
        assert hasattr(ResearchAction, "target_snapshot")
        assert hasattr(ResearchAction, "build_target_snapshot")
        assert hasattr(ResearchAction, "target_snapshot_data")

    def test_target_snapshot_roundtrip(self, app, db_session):
        case = _orm_case()
        subject = Subject(name="Snap Person", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.flush()
        action = ResearchAction(
            case_id=case.id,
            subject_id=subject.id,
            target_kind="subject",
            action_type="email",
            data_value="snap@example.com",
            label="Email",
            status="pending",
        )
        action.target_snapshot = json.dumps(
            action.build_target_snapshot(subject, "snap@example.com")
        )
        db.session.add(action)
        db.session.commit()
        assert action.target_kind == "subject"
        assert action.subject_id == subject.id
        snap = action.target_snapshot_data
        assert snap["subject_id"] == subject.id
        assert snap["data_value"] == "snap@example.com"
        assert snap["name"] == "Snap Person"
        assert snap["subject_type"] == "person"

    def test_case_wide_snapshot(self, app, db_session):
        case = _orm_case()
        action = ResearchAction(
            case_id=case.id,
            target_kind="case",
            action_type="google_dork",
            data_value="site:example.nl",
            label="Dork",
            status="pending",
        )
        action.target_snapshot = json.dumps(action.build_target_snapshot(None, "q"))
        db.session.add(action)
        db.session.commit()
        assert action.subject_id is None
        assert action.target_kind == "case"
        assert action.target_snapshot_data["subject_id"] is None


class TestRunActionSubjectValidation:
    def test_case_wide_action(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={"action_type": "google_dork", "data_value": "site:example.nl"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        action = db.session.get(ResearchAction, body["id"])
        assert action.target_kind == "case"
        assert action.subject_id is None
        assert action.target_snapshot_data["subject_id"] is None

    def test_subject_scoped_action(self, auth_client):
        case = _create_case_with_subject(auth_client)
        subject = case.subjects.first()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "PR4 Person",
                "subject_id": str(subject.id),
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        action = db.session.get(ResearchAction, body["id"])
        assert action.target_kind == "subject"
        assert action.subject_id == subject.id
        assert action.target_snapshot_data["subject_id"] == subject.id
        assert action.target_snapshot_data["name"] == "PR4 Person"

    def test_unlinked_subject_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        other = _create_case_with_subject(auth_client, title="PR4 Other Case")
        other_subject = other.subjects.first()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "X",
                "subject_id": str(other_subject.id),
            },
        )
        assert resp.status_code == 400
        assert "not linked" in resp.get_json()["error"]

    def test_unknown_subject_rejected(self, auth_client):
        case = _create_case_with_subject(auth_client)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={"action_type": "osint", "data_value": "X", "subject_id": "nope"},
        )
        assert resp.status_code == 404

    def test_same_action_runs_per_subject(self, auth_client):
        """Dedupe (409) is scoped per subject: a running action for one subject
        must not block the same action for another subject."""
        case = _create_case_with_subject(auth_client)
        subject_a = case.subjects.first()
        subject_b = Subject(name="PR4 Person B", subject_type="person")
        db.session.add(subject_b)
        db.session.flush()
        case.subjects.append(subject_b)
        running = ResearchAction(
            case_id=case.id,
            subject_id=subject_a.id,
            target_kind="subject",
            action_type="osint",
            data_value="A",
            label="OSINT",
            status="running",
        )
        db.session.add(running)
        db.session.commit()

        same_subject = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "A",
                "subject_id": str(subject_a.id),
            },
        )
        assert same_subject.status_code == 409

        other_subject = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/run-action",
            json={
                "action_type": "osint",
                "data_value": "B",
                "subject_id": str(subject_b.id),
            },
        )
        assert other_subject.status_code == 200


class TestActionSubjectResolution:
    def test_subject_scoped_resolution(self, app, db_session):
        case = _orm_case()
        subject = Subject(name="Resolve Person", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        action = ResearchAction(
            case_id=case.id,
            subject_id=subject.id,
            target_kind="subject",
            action_type="email",
            data_value="r@example.com",
            label="Email",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()
        resolved = _action_subject(action)
        assert resolved is not None
        assert resolved.id == subject.id

    def test_case_wide_resolution_is_none(self, app, db_session):
        case = _orm_case()
        subject = Subject(name="CaseWide Person", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        action = ResearchAction(
            case_id=case.id,
            target_kind="case",
            action_type="osint",
            data_value="x",
            label="OSINT",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()
        assert _action_subject(action) is None


class TestSubjectPresets:
    def test_presets_cover_subject_types(self):
        for stype in (
            "person",
            "company",
            "organization",
            "vehicle",
            "vessel",
            "online",
        ):
            assert stype in SUBJECT_TYPE_PRESETS
            assert isinstance(presets_for_subject(stype), list)
            assert len(presets_for_subject(stype)) > 0

    def test_preset_unknown_type_falls_back(self):
        assert presets_for_subject("unknown_type") == []
