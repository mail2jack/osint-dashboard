"""Tests for Subject Profile Activity Trail endpoint (PR #64).

Covers: GET /cms/api/profile/subjects/<id>/activity — read-only timeline
that merges AuditLog and ResearchAction entries for a subject.
"""

import uuid
from datetime import UTC, datetime

from cms.models import (
    AuditLog,
    Case,
    Client,
    FeatureFlag,
    ResearchAction,
    Subject,
    User,
    db,
)


def _make_user(role="investigator"):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=f"at_{token}",
        email=f"at_{token}@localhost",
        full_name="Activity Trail User",
        role=role,
        is_active=True,
    )
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.commit()
    return user


def _login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    return client


def _enable_flag(tenant_id):
    flag = FeatureFlag(
        tenant_id=tenant_id,
        flag_name="subject_first_investigations",
        enabled=True,
    )
    db.session.add(flag)
    db.session.commit()
    return flag


def _make_case(owner):
    client = Client(name="AT Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"AT-{uuid.uuid4().hex[:6]}",
        client_id=client.id,
        title="Activity Trail Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=owner.id,
    )
    db.session.add(case)
    db.session.commit()
    return case


def _make_subject(tenant_id):
    subject = Subject(
        name=f"AT Subject {uuid.uuid4().hex[:6]}",
        subject_type="person",
        tenant_id=tenant_id,
    )
    db.session.add(subject)
    db.session.commit()
    return subject


def _activity_url(subject_id):
    return f"/cms/api/profile/subjects/{subject_id}/activity"


class TestActivityTrail:
    def test_empty_trail(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        subject = _make_subject(admin.tenant_id)

        resp = auth_client.get(_activity_url(subject.id))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_trail_includes_audit_log_entries(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        subject = _make_subject(admin.tenant_id)

        AuditLog.log(
            user_id=admin.id,
            action="update",
            entity_type="subject",
            entity_id=subject.id,
            description=f"Updated subject {subject.name}",
        )
        db.session.commit()

        resp = auth_client.get(_activity_url(subject.id))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        actions = [i["action"] for i in data["items"]]
        assert "update" in actions

    def test_trail_includes_research_actions(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        subject = _make_subject(admin.tenant_id)
        case = _make_case(admin)

        action = ResearchAction(
            case_id=case.id,
            subject_id=subject.id,
            action_type="email",
            status="completed",
            label="Email Lookup",
            tenant_id=admin.tenant_id,
            created_by=admin.id,
        )
        db.session.add(action)
        db.session.commit()

        resp = auth_client.get(_activity_url(subject.id))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        sources = [i["source"] for i in data["items"]]
        assert "action" in sources

    def test_trail_sorted_by_timestamp_desc(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        subject = _make_subject(admin.tenant_id)

        AuditLog.log(
            user_id=admin.id,
            action="create",
            entity_type="subject",
            entity_id=subject.id,
            description="First entry",
        )
        db.session.commit()

        AuditLog.log(
            user_id=admin.id,
            action="update",
            entity_type="subject",
            entity_id=subject.id,
            description="Second entry",
        )
        db.session.commit()

        resp = auth_client.get(_activity_url(subject.id))
        data = resp.get_json()
        assert data["total"] >= 2
        timestamps = [i["timestamp"] for i in data["items"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_trail_excludes_other_subjects(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        subject_a = _make_subject(admin.tenant_id)
        subject_b = _make_subject(admin.tenant_id)

        AuditLog.log(
            user_id=admin.id,
            action="update",
            entity_type="subject",
            entity_id=subject_b.id,
            description=f"Updated {subject_b.name}",
        )
        db.session.commit()

        resp = auth_client.get(_activity_url(subject_a.id))
        data = resp.get_json()
        descriptions = [i["description"] for i in data["items"]]
        assert all(subject_b.name not in d for d in descriptions)

    def test_trail_404_without_feature_flag(self, client):
        admin = User.query.filter_by(role="admin").first()
        subject = _make_subject(admin.tenant_id)

        user = _make_user()
        _login_as(client, user)

        resp = client.get(_activity_url(subject.id))
        assert resp.status_code == 404

    def test_trail_404_for_nonexistent_subject(self, auth_client):
        resp = auth_client.get(
            "/cms/api/profile/subjects/nonexistent-id/activity",
        )
        assert resp.status_code == 404

    def test_trail_includes_user_name(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        subject = _make_subject(admin.tenant_id)

        AuditLog.log(
            user_id=admin.id,
            action="create",
            entity_type="subject_identifier",
            entity_id=subject.id,
            description="Added identifier",
        )
        db.session.commit()

        resp = auth_client.get(_activity_url(subject.id))
        data = resp.get_json()
        assert data["total"] >= 1
        user_names = [i["user_name"] for i in data["items"]]
        assert any(admin.full_name in n for n in user_names)
