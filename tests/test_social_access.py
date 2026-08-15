"""Access-hardening tests for social accounts, relations and case membership.

Covers P1 commit 1:
- ``subject_access_required`` on add/delete social account (soft-delete 404, cross-tenant 403)
- ``save_finding_as_social_account`` always validates the resolved subject
- case-level access checks on ``save_username_findings`` / ``create_subject_from_username``
- finding ownership check on ``extract_social_id``
- soft-deleted subjects rejected in case-membership routes
"""

import uuid
from datetime import UTC, datetime

from cms.models import (
    Case,
    Client,
    Finding,
    SocialAccount,
    Subject,
    Tenant,
    User,
    db,
)


def _make_user(username, role="investigator", app=None):
    user = User(
        username=username,
        email=f"{username}@localhost",
        full_name=username,
        role=role,
        is_active=True,
    )
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.commit()
    return user


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


def _make_tenant_b():
    tag = uuid.uuid4().hex[:8]
    tenant_b = Tenant(
        name=f"Tenant B {tag}",
        slug=f"tenant-b-{tag}",
        is_active=True,
        tier="enterprise",
        join_code=f"tenant-b-code-{tag}",
    )
    db.session.add(tenant_b)
    db.session.flush()
    return tenant_b


def _make_case(owner=None):
    client = Client(
        name="Test Client",
        contact_person="Test",
        contact_email="test@test.nl",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        client_id=client.id,
        title="Access Test Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=owner.id if owner else 1,
    )
    db.session.add(case)
    db.session.flush()
    return case


class TestSocialAccountAccess:
    def test_add_social_account_deleted_subject_404(self, auth_client):
        subject = Subject(name="Deleted Subject", subject_type="person")
        subject.is_deleted = True
        db.session.add(subject)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/api/subjects/{subject.id}/social-accounts",
            json={"platform": "GitHub", "username": "ghost"},
        )
        assert resp.status_code == 404

    def test_add_social_account_cross_tenant_403(self, app, auth_client):
        tenant_b = _make_tenant_b()
        subject_b = Subject(
            tenant_id=tenant_b.id, name="Tenant B Subject", subject_type="person"
        )
        db.session.add(subject_b)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            f"/cms/api/subjects/{subject_b.id}/social-accounts",
            json={"platform": "GitHub", "username": "testuser"},
        )
        assert resp.status_code == 403

    def test_add_social_account_same_tenant_unassigned_allowed(self, app, auth_client):
        subject = Subject(name="Fresh Subject", subject_type="person")
        db.session.add(subject)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            f"/cms/api/subjects/{subject.id}/social-accounts",
            json={"platform": "GitHub", "username": "testuser"},
        )
        assert resp.status_code == 201

    def test_delete_social_account_cross_tenant_403(self, app, auth_client):
        tenant_b = _make_tenant_b()
        subject_b = Subject(
            tenant_id=tenant_b.id, name="Tenant B Subject", subject_type="person"
        )
        db.session.add(subject_b)
        db.session.flush()
        account = SocialAccount(
            subject_id=subject_b.id, platform="github", username="testuser"
        )
        db.session.add(account)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.delete(
            f"/cms/api/subjects/{subject_b.id}/social-accounts/{account.id}"
        )
        # Non-JSON mutating request -> decorator denies via flash+redirect
        assert resp.status_code in (302, 403)
        assert resp.status_code != 200

    def test_delete_social_account_deleted_subject_404(self, app, auth_client):
        subject = Subject(name="Deleted Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        account = SocialAccount(
            subject_id=subject.id, platform="github", username="testuser"
        )
        db.session.add(account)
        subject.is_deleted = True
        db.session.commit()

        resp = auth_client.delete(
            f"/cms/api/subjects/{subject.id}/social-accounts/{account.id}"
        )
        assert resp.status_code == 404


class TestSaveFindingAsSocialAccountAccess:
    def test_subject_only_cross_tenant_403(self, app, auth_client):
        tenant_b = _make_tenant_b()
        subject_b = Subject(
            tenant_id=tenant_b.id, name="Tenant B Subject", subject_type="person"
        )
        db.session.add(subject_b)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={"subject_id": str(subject_b.id), "url": "https://github.com/x"},
        )
        assert resp.status_code == 403

    def test_finding_cross_tenant_403(self, app, auth_client):
        tenant_b = _make_tenant_b()
        subject_b = Subject(
            tenant_id=tenant_b.id, name="Tenant B Subject", subject_type="person"
        )
        db.session.add(subject_b)
        db.session.flush()
        case = _make_case(owner=None)
        case.tenant_id = tenant_b.id
        db.session.flush()
        finding = Finding(
            tenant_id=tenant_b.id,
            case_id=case.id,
            subject_id=subject_b.id,
            title="TB Finding",
            content="content",
            source_url="https://github.com/x",
            source_type="osint",
            finding_type="identity",
            created_by=1,
        )
        db.session.add(finding)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={"finding_id": str(finding.id), "url": "https://github.com/x"},
        )
        assert resp.status_code == 403

    def test_finding_linked_to_deleted_subject_404(self, app, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="Deleted Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        finding = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Finding on deleted subject",
            content="content",
            source_url="https://github.com/x",
            source_type="osint",
            finding_type="identity",
            created_by=1,
        )
        db.session.add(finding)
        subject.is_deleted = True
        db.session.commit()

        resp = auth_client.post(
            "/cms/api/findings/save-as-social-account",
            json={"finding_id": str(finding.id), "url": "https://github.com/x"},
        )
        assert resp.status_code == 404

    def test_same_tenant_allowed(self, app, auth_client):
        subject = Subject(name="Fresh Subject", subject_type="person")
        db.session.add(subject)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={"subject_id": str(subject.id), "url": "https://github.com/x"},
        )
        assert resp.status_code == 201


class TestUsernameFindingsCaseAccess:
    def test_save_username_findings_case_no_access_403(self, app, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="Subject", subject_type="person")
        db.session.add(subject)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            f"/cms/api/subjects/{subject.id}/save-username-findings",
            json={
                "case_id": str(case.id),
                "results": [
                    {
                        "platform": "GitHub",
                        "url": "https://github.com/testuser",
                        "username": "testuser",
                    }
                ],
            },
        )
        assert resp.status_code == 403

    def test_save_username_findings_case_assigned_allowed(self, app, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="Subject", subject_type="person")
        db.session.add(subject)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        case.investigators.append(user)
        db.session.commit()
        client = _login_as(app.test_client(), user)

        resp = client.post(
            f"/cms/api/subjects/{subject.id}/save-username-findings",
            json={
                "case_id": str(case.id),
                "results": [
                    {
                        "platform": "GitHub",
                        "url": "https://github.com/testuser",
                        "username": "testuser",
                    }
                ],
            },
        )
        assert resp.status_code == 201

    def test_create_subject_from_username_case_no_access_403(self, app, auth_client):
        case = _make_case(owner=None)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            "/cms/api/subjects/create-from-username",
            json={
                "username": "testuser",
                "platform": "GitHub",
                "case_id": str(case.id),
            },
        )
        assert resp.status_code == 403


class TestExtractSocialIdFindingAccess:
    def test_extract_social_id_cross_tenant_finding_403(self, app, auth_client):
        tenant_b = _make_tenant_b()
        subject_b = Subject(
            tenant_id=tenant_b.id, name="Tenant B Subject", subject_type="person"
        )
        db.session.add(subject_b)
        db.session.flush()
        case = _make_case(owner=None)
        case.tenant_id = tenant_b.id
        db.session.flush()
        finding = Finding(
            tenant_id=tenant_b.id,
            case_id=case.id,
            subject_id=subject_b.id,
            title="TB Finding",
            content="content",
            source_url="https://github.com/x",
            source_type="osint",
            finding_type="identity",
            created_by=1,
        )
        db.session.add(finding)
        db.session.commit()

        user = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        client = _login_as(app.test_client(), user)

        resp = client.post(
            "/cms/extract-social-id",
            json={"url": "https://example.com", "finding_id": str(finding.id)},
        )
        assert resp.status_code == 403


class TestCaseMembershipDeletedSubject:
    def test_add_subject_to_case_deleted_404(self, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="Deleted Subject", subject_type="person")
        subject.is_deleted = True
        db.session.add(subject)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/cases/{case.id}/add-subject",
            json={"subject_id": str(subject.id)},
        )
        assert resp.status_code == 404

    def test_bulk_add_subjects_skips_deleted(self, auth_client):
        case = _make_case(owner=None)
        alive = Subject(name="Alive Subject", subject_type="person")
        gone = Subject(name="Gone Subject", subject_type="person")
        gone.is_deleted = True
        db.session.add_all([alive, gone])
        db.session.commit()

        resp = auth_client.post(
            f"/cms/cases/{case.id}/add-subjects-bulk",
            json={"subject_ids": [str(alive.id), str(gone.id)]},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["added"] == ["Alive Subject"]
        assert any(s["id"] == str(gone.id) for s in data["skipped"])
