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


class TestViewerCannotMutate:
    """Viewers may read subjects but never mutate social data (P1 review).

    ``staff_required`` gates every mutating social-account/extract route:
    a viewer with full case access still gets 403 and nothing is written.
    """

    def _viewer_with_subject(self, app, subject=None, social_media_ids=None):
        if subject is None:
            subject = Subject(
                name="Subject",
                subject_type="person",
                social_media_ids=social_media_ids,
            )
        db.session.add(subject)
        viewer = _make_user(f"viewer_{uuid.uuid4().hex[:8]}", role="viewer")
        case = _make_case(owner=viewer)
        case.subjects.append(subject)
        db.session.commit()
        return viewer, case, subject

    def test_viewer_add_social_account_403(self, app, auth_client):
        viewer, _case, subject = self._viewer_with_subject(app)
        client = _login_as(app.test_client(), viewer)

        before = SocialAccount.query.count()
        resp = client.post(
            f"/cms/api/subjects/{subject.id}/social-accounts",
            json={"platform": "GitHub", "username": "ghost"},
        )
        assert resp.status_code == 403
        assert SocialAccount.query.count() == before

    def test_viewer_delete_social_account_403(self, app, auth_client):
        subject = Subject(name="Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        account = SocialAccount(
            subject_id=subject.id, platform="github", username="testuser"
        )
        db.session.add(account)
        viewer, _case, subject = self._viewer_with_subject(app, subject=subject)
        client = _login_as(app.test_client(), viewer)

        resp = client.delete(
            f"/cms/api/subjects/{subject.id}/social-accounts/{account.id}"
        )
        assert resp.status_code in (302, 403)
        assert resp.status_code != 200
        assert db.session.get(SocialAccount, account.id) is not None

    def test_viewer_save_finding_as_social_account_403(self, app, auth_client):
        viewer, _case, subject = self._viewer_with_subject(app)
        client = _login_as(app.test_client(), viewer)

        before = SocialAccount.query.count()
        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={"subject_id": str(subject.id), "url": "https://github.com/x"},
        )
        assert resp.status_code == 403
        assert SocialAccount.query.count() == before

    def test_viewer_save_username_findings_403(self, app, auth_client):
        viewer, case, subject = self._viewer_with_subject(app)
        client = _login_as(app.test_client(), viewer)

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
        assert Finding.query.filter_by(subject_id=subject.id).count() == 0

    def test_viewer_create_subject_from_username_403(self, app, auth_client):
        viewer = _make_user(f"viewer_{uuid.uuid4().hex[:8]}", role="viewer")
        client = _login_as(app.test_client(), viewer)

        before = Subject.query.count()
        resp = client.post(
            "/cms/api/subjects/create-from-username",
            json={"username": "testuser", "platform": "GitHub"},
        )
        assert resp.status_code == 403
        assert Subject.query.count() == before

    def test_viewer_extract_social_id_403(self, app, auth_client):
        viewer, _case, subject = self._viewer_with_subject(app)
        client = _login_as(app.test_client(), viewer)

        resp = client.post(
            "/cms/extract-social-id",
            json={"url": "https://example.com", "subject_id": str(subject.id)},
        )
        assert resp.status_code == 403

    def test_viewer_bulk_extract_social_ids_403(self, app, auth_client):
        viewer, _case, subject = self._viewer_with_subject(app)
        client = _login_as(app.test_client(), viewer)

        resp = client.post(
            f"/cms/subjects/{subject.id}/bulk-extract-social-ids", json={}
        )
        assert resp.status_code == 403

    def test_viewer_update_subject_social_ids_403(self, app, auth_client):
        viewer, _case, subject = self._viewer_with_subject(
            app, social_media_ids={"instagram": "old"}
        )
        client = _login_as(app.test_client(), viewer)

        resp = client.put(
            f"/cms/subjects/{subject.id}/social-ids",
            json={"social_media_ids": {"instagram": "new"}},
        )
        assert resp.status_code == 403
        assert subject.social_media_ids == {"instagram": "old"}


class TestCaseExportJsonFiltersFindings:
    def test_export_json_excludes_deleted_and_archived_findings(self, app, auth_client):
        user = _make_user(f"senior_{uuid.uuid4().hex[:8]}", role="senior_investigator")
        client = _login_as(app.test_client(), user)
        case = _make_case(owner=user)
        subject = Subject(name="Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.flush()

        active = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Active",
            content="content",
            source_type="osint",
            finding_type="identity",
            created_by=user.id,
        )
        deleted = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Deleted",
            content="content",
            source_type="osint",
            finding_type="identity",
            created_by=user.id,
        )
        deleted.is_deleted = True
        archived = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Archived",
            content="content",
            source_type="osint",
            finding_type="identity",
            created_by=user.id,
            archived_at=datetime.now(UTC),
        )
        db.session.add_all([active, deleted, archived])
        db.session.commit()

        resp = client.get(f"/cms/cases/{case.id}/export-json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert [f["title"] for f in data["findings"]] == ["Active"]


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


class TestStaffCrossCaseSubjectAccess:
    """Staff may not mutate a same-tenant subject they have no case access to.

    An investigator assigned to case A must get 403 when mutating (or
    extracting on) a subject linked only to case B — the routes must check
    case-level subject access, not just tenant ownership.
    """

    def _inv_with_case_a_and_subject_b(self, app):
        inv = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        case_a = _make_case(owner=inv)
        owner_b = _make_user(f"inv_b_{uuid.uuid4().hex[:8]}")
        case_b = _make_case(owner=owner_b)
        subject_b = Subject(name="Case B Subject", subject_type="person")
        db.session.add(subject_b)
        case_b.subjects.append(subject_b)
        db.session.commit()
        return inv, case_a, case_b, subject_b

    def test_save_finding_as_social_account_cross_case_403(self, app, auth_client):
        inv, _case_a, _case_b, subject_b = self._inv_with_case_a_and_subject_b(app)
        client = _login_as(app.test_client(), inv)

        before = SocialAccount.query.count()
        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={"subject_id": str(subject_b.id), "url": "https://github.com/x"},
        )
        assert resp.status_code == 403
        assert SocialAccount.query.count() == before

    def test_save_username_findings_cross_case_403(self, app, auth_client):
        inv, _case_a, _case_b, subject_b = self._inv_with_case_a_and_subject_b(app)
        client = _login_as(app.test_client(), inv)

        resp = client.post(
            f"/cms/api/subjects/{subject_b.id}/save-username-findings",
            json={
                "results": [
                    {
                        "platform": "GitHub",
                        "url": "https://github.com/testuser",
                        "username": "testuser",
                    }
                ]
            },
        )
        assert resp.status_code == 403
        assert Finding.query.filter_by(subject_id=subject_b.id).count() == 0

    def test_extract_social_id_cross_case_403(self, app, auth_client):
        inv, _case_a, _case_b, subject_b = self._inv_with_case_a_and_subject_b(app)
        client = _login_as(app.test_client(), inv)

        resp = client.post(
            "/cms/extract-social-id",
            json={"url": "https://example.com", "subject_id": str(subject_b.id)},
        )
        assert resp.status_code == 403

    def test_bulk_extract_social_ids_cross_case_403(self, app, auth_client):
        inv, _case_a, _case_b, subject_b = self._inv_with_case_a_and_subject_b(app)
        client = _login_as(app.test_client(), inv)

        resp = client.post(
            f"/cms/subjects/{subject_b.id}/bulk-extract-social-ids", json={}
        )
        assert resp.status_code == 403
        assert SocialAccount.query.filter_by(subject_id=subject_b.id).count() == 0

    def test_update_subject_social_ids_cross_case_403(self, app, auth_client):
        inv, _case_a, _case_b, subject_b = self._inv_with_case_a_and_subject_b(app)
        subject_b.social_media_ids = {"instagram": "old"}
        db.session.commit()
        client = _login_as(app.test_client(), inv)

        resp = client.put(
            f"/cms/subjects/{subject_b.id}/social-ids",
            json={"social_media_ids": {"instagram": "new"}},
        )
        assert resp.status_code == 403
        assert subject_b.social_media_ids == {"instagram": "old"}

    def test_access_allowed_with_case_access(self, app, auth_client):
        inv = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        case = _make_case(owner=inv)
        subject = Subject(name="Case A Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        client = _login_as(app.test_client(), inv)

        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={"subject_id": str(subject.id), "url": "https://github.com/positive"},
        )
        assert resp.status_code == 201


class TestFindingSubjectConsistency:
    """A body-supplied subject_id must match the finding/case when finding_id is used."""

    def _finding_with_subject(self, app, owner):
        case = _make_case(owner=owner)
        sx = Subject(name="SX", subject_type="person")
        sy = Subject(name="SY", subject_type="person")
        db.session.add_all([sx, sy])
        case.subjects.append(sx)
        case.subjects.append(sy)
        db.session.flush()
        finding = Finding(
            case_id=case.id,
            subject_id=sx.id,
            title="Finding",
            content="content",
            source_url="https://example.com",
            source_type="osint",
            finding_type="identity",
            created_by=owner.id,
        )
        db.session.add(finding)
        db.session.commit()
        return case, sx, sy, finding

    def test_save_finding_as_social_account_mismatch_400(self, app, auth_client):
        inv = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        _case, _sx, sy, finding = self._finding_with_subject(app, inv)
        client = _login_as(app.test_client(), inv)

        before = SocialAccount.query.count()
        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={
                "finding_id": str(finding.id),
                "subject_id": str(sy.id),
                "url": "https://github.com/x",
            },
        )
        assert resp.status_code == 400
        assert SocialAccount.query.count() == before

    def test_save_finding_as_social_account_match_allowed(self, app, auth_client):
        inv = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        _case, sx, _sy, finding = self._finding_with_subject(app, inv)
        client = _login_as(app.test_client(), inv)

        resp = client.post(
            "/cms/api/findings/save-as-social-account",
            json={
                "finding_id": str(finding.id),
                "subject_id": str(sx.id),
                "url": "https://github.com/x",
            },
        )
        assert resp.status_code == 201

    def test_extract_social_id_mismatch_400(self, app, auth_client):
        inv = _make_user(f"inv_{uuid.uuid4().hex[:8]}")
        _case, _sx, sy, finding = self._finding_with_subject(app, inv)
        client = _login_as(app.test_client(), inv)

        resp = client.post(
            "/cms/extract-social-id",
            json={
                "url": "https://example.com",
                "finding_id": str(finding.id),
                "subject_id": str(sy.id),
            },
        )
        assert resp.status_code == 400
