"""Regression tests for the privileged duplicate-subject merge flow."""

import uuid
from datetime import UTC, datetime

from cms.models import Case, Client, Subject, User, case_subjects, db


def _make_user(role="investigator"):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=f"merge_{token}",
        email=f"merge_{token}@localhost",
        full_name="Merge Test User",
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


def _make_case(owner):
    client = Client(name="Merge Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"MERGE-{uuid.uuid4().hex[:8]}",
        client_id=client.id,
        title="Merge case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=owner.id,
    )
    db.session.add(case)
    db.session.flush()
    return case


class TestSubjectMerge:
    def test_investigator_cannot_merge_subjects(self, app):
        investigator = _make_user()
        case = _make_case(investigator)
        target = Subject(name="Target", subject_type="person")
        source = Subject(name="Source", subject_type="person")
        db.session.add_all([target, source])
        case.subjects.extend([target, source])
        db.session.commit()

        response = _login_as(app.test_client(), investigator).post(
            f"/cms/subjects/{target.id}/merge", json={"source_id": source.id}
        )

        assert response.status_code == 403
        assert db.session.get(Subject, source.id).is_deleted is False

    def test_merge_keeps_one_link_when_subjects_share_case(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        case = _make_case(admin)
        target = Subject(name="Target", subject_type="person")
        source = Subject(name="Source", subject_type="person")
        db.session.add_all([target, source])
        case.subjects.extend([target, source])
        db.session.commit()

        response = auth_client.post(
            f"/cms/subjects/{target.id}/merge", json={"source_id": source.id}
        )

        assert response.status_code == 200
        assert db.session.get(Subject, source.id).is_deleted is True
        links = db.session.execute(
            case_subjects.select().where(case_subjects.c.case_id == case.id)
        ).fetchall()
        assert len(links) == 1
        assert links[0].subject_id == target.id
