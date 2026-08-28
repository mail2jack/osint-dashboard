"""
ADR-0001 PR7a — tabbed Subject Profile (read side, behind the
``subject_first_investigations`` feature flag).

Covers the flag gate (off -> legacy redirect, on -> profile render), the
``SubjectService.profile_view`` read-model (identifiers/facts/relations/
cases/actions/findings + provenance), findings integrity, viewer redaction,
and case-level access control on the new route.
"""

import json
from datetime import datetime, timezone

from cms.models import (
    ActionFinding,
    Address,
    Case,
    Contact,
    FeatureFlag,
    Finding,
    ResearchAction,
    SocialAccount,
    Subject,
    SubjectFact,
    SubjectIdentifier,
    User,
    db,
    subject_relations,
)


def _enable_flag(tenant_id):
    flag = FeatureFlag(
        tenant_id=tenant_id,
        flag_name="subject_first_investigations",
        enabled=True,
    )
    db.session.add(flag)
    db.session.commit()
    return flag


def _admin():
    return User.query.filter_by(username="admin").first()


def _case_with_subject(auth_client, title="Profile Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "Profile Client",
            "title": title,
            "subject_0_name": "Profile Person",
            "subject_0_type": "person",
            "subject_0_email": "person@example.com",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    case = Case.query.filter_by(title=title).first()
    assert case is not None
    return case


def _make_user(username, role, tenant_id):
    user = User(
        username=username,
        email=f"{username}@localhost",
        full_name=username.title(),
        role=role,
        is_active=True,
        tenant_id=tenant_id,
    )
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.commit()
    return user


class TestFlagGate:
    def test_flag_off_redirects_to_legacy(self, auth_client):
        case = _case_with_subject(auth_client, title="Profile Gate Off")
        subject = case.subjects[0]
        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/cms/subjects/{subject.id}")

        legacy = auth_client.get(f"/cms/subjects/{subject.id}")
        assert legacy.status_code == 200
        assert "Subject Profile" not in legacy.get_data(as_text=True)

    def test_flag_on_renders_all_tabs(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Profile Tabs")
        subject = case.subjects[0]

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert subject.name in html
        for tab in (
            "Overview",
            "Identity",
            "Contact",
            "Addresses",
            "Financial",
            "Online",
            "Relations",
            "Investigation",
            "Facts",
            "Findings",
        ):
            assert tab in html
        assert case.case_number in html
        assert "Open case" in html

        legacy = auth_client.get(f"/cms/subjects/{subject.id}")
        assert legacy.status_code == 200
        assert "Subject Profile" in legacy.get_data(as_text=True)


class TestReadModel:
    def test_child_data_and_provenance(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Profile Read Model")
        subject = case.subjects[0]
        now = datetime.now(timezone.utc)

        other = Subject(
            name="Family Member", subject_type="person", created_by=admin.id
        )
        db.session.add(other)
        db.session.flush()

        addr = Address(
            subject_id=subject.id,
            street="Teststraat",
            number="12",
            zipcode="1234AB",
            town="Utrecht",
            is_primary=True,
        )
        addr.encrypt_fields()
        db.session.add(addr)

        contact = Contact(
            subject_id=subject.id,
            contact_type="email",
            value="probe@example.com",
            is_primary=True,
        )
        contact.encrypt_fields()
        db.session.add(contact)

        db.session.add(
            SocialAccount(
                tenant_id=admin.tenant_id,
                subject_id=subject.id,
                platform="twitter",
                username="probeuser",
                url="https://twitter.com/probeuser",
            )
        )

        ident = SubjectIdentifier(
            subject_id=subject.id,
            identifier_type="email",
            status="verified",
            source="company register",
            reliability="high",
            created_by=admin.id,
        )
        ident.set_value("probe@example.com")
        db.session.add(ident)

        fact = SubjectFact(
            subject_id=subject.id,
            fact_key="income",
            status="verified",
            source="interview",
            reliability="high",
            verified_by=admin.id,
            verified_at=now,
            created_by=admin.id,
        )
        fact.set_value("2000 EUR")
        db.session.add(fact)

        db.session.execute(
            subject_relations.insert().values(
                subject_id=subject.id,
                related_subject_id=other.id,
                relation_type="family",
                direction="outgoing",
                source="case file",
                reliability="high",
                status="verified",
                case_number=case.case_number,
                created_by=admin.id,
            )
        )

        action = ResearchAction(
            tenant_id=admin.tenant_id,
            case_id=case.id,
            subject_id=subject.id,
            action_type="google_dork",
            label="probe dork",
            status="completed",
            result_summary="2 hits",
            created_by=admin.id,
            target_snapshot=json.dumps(
                {"subject_id": subject.id, "data_value": "probe"}
            ),
        )
        db.session.add(action)
        db.session.flush()

        finding = Finding(
            tenant_id=admin.tenant_id,
            case_id=case.id,
            subject_id=subject.id,
            title="Found asset",
            content="Registered under subject",
            source_url="https://example.com/asset",
            source_type="osint",
            status="verified",
            verified_by=admin.id,
            verified_at=now,
            created_by=admin.id,
        )
        db.session.add(finding)
        db.session.flush()
        finding.content_hash = finding.compute_hash()
        db.session.flush()
        db.session.add(ActionFinding(action_id=action.id, finding_id=finding.id))
        db.session.commit()

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Addresses / contacts / social / identifiers / facts
        assert "Teststraat 12" in html
        assert "1234AB" in html
        assert "probe@example.com" in html
        assert "probeuser" in html
        assert "2000 EUR" in html
        assert "income" in html
        # Provenance chips
        assert "interview" in html
        assert "company register" in html
        assert "verified by" in html
        assert admin.username in html

        # Relations
        assert other.name in html
        assert "family" in html
        assert "case file" in html

        # Linked case
        assert case.case_number in html

        # Research action
        assert "probe dork" in html
        assert "google_dork" in html

        # Finding -> register deep link (findings list moved to register)
        assert f"/cms/workflow/findings?subject_id={subject.id}" in html


class TestFindingsIntegrity:
    def test_intact_and_tampered(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Profile Integrity")
        subject = case.subjects[0]

        intact = Finding(
            tenant_id=admin.tenant_id,
            case_id=case.id,
            subject_id=subject.id,
            title="Intact finding",
            content="Original content",
            source_type="osint",
            status="candidate",
            created_by=admin.id,
        )
        intact.content_hash = intact.compute_hash()

        tampered = Finding(
            tenant_id=admin.tenant_id,
            case_id=case.id,
            subject_id=subject.id,
            title="Tampered finding",
            content="Original content",
            source_type="document",
            status="candidate",
            created_by=admin.id,
        )
        db.session.add_all([intact, tampered])
        db.session.flush()
        intact.content_hash = intact.compute_hash()
        tampered.content_hash = tampered.compute_hash()
        db.session.commit()

        tampered.content = "Modified after the fact"
        db.session.commit()

        # Integrity indicators moved to the central register (Fase D).
        resp = auth_client.get(f"/cms/workflow/findings?subject_id={subject.id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Intact finding" in html
        assert "Tampered finding" in html
        assert "Intact" in html
        assert "Tampered" in html


class TestAccessControl:
    def test_nonexistent_subject_404(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        resp = auth_client.get("/cms/subjects/no-such-id/profile")
        assert resp.status_code == 404

    def test_non_admin_without_case_access_is_denied(self, app, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Profile Access")
        subject = case.subjects[0]
        analyst = _make_user("profile_analyst", "investigator", admin.tenant_id)

        viewer_client = app.test_client()
        with viewer_client.session_transaction() as sess:
            sess["_user_id"] = str(analyst.id)
            sess["_fresh"] = True
            sess["_remember"] = "set"

        resp = viewer_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/cms/subjects")


class TestViewerRedaction:
    def test_viewer_values_redacted_admin_plain(self, app, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)

        subject = Subject(
            name="Redaction Subject", subject_type="person", created_by=admin.id
        )
        db.session.add(subject)
        db.session.flush()

        contact = Contact(
            subject_id=subject.id,
            contact_type="email",
            value="jane@example.com",
            is_primary=True,
        )
        contact.encrypt_fields()
        db.session.add(contact)

        ident = SubjectIdentifier(
            subject_id=subject.id,
            identifier_type="bsn",
            status="candidate",
            created_by=admin.id,
        )
        ident.set_value("123456789")
        db.session.add(ident)
        db.session.commit()

        admin_resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert admin_resp.status_code == 200
        admin_html = admin_resp.get_data(as_text=True)
        assert "jane@example.com" in admin_html
        assert "123456789" in admin_html

        viewer = _make_user("profile_viewer", "viewer", admin.tenant_id)
        viewer_client = app.test_client()
        with viewer_client.session_transaction() as sess:
            sess["_user_id"] = str(viewer.id)
            sess["_fresh"] = True
            sess["_remember"] = "set"

        resp = viewer_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "jane@example.com" not in html
        assert "123456789" not in html
        assert "j***@example.com" in html
        assert "12345****" in html


class TestSocialContacts:
    def test_create_social_contact_requires_platform(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Social Platform Required")
        subject = case.subjects[0]

        resp = auth_client.post(
            f"/cms/api/profile/subjects/{subject.id}/contacts",
            json={"contact_type": "social", "value": "some.user"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]

    def test_create_social_contact_persists_platform(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Social Create")
        subject = case.subjects[0]

        resp = auth_client.post(
            f"/cms/api/profile/subjects/{subject.id}/contacts",
            json={
                "contact_type": "social",
                "platform": "Twitter / X",
                "value": "twitter.handle",
                "is_primary": True,
            },
        )
        assert resp.status_code == 201
        item = resp.get_json()["item"]
        assert item["contact_type"] == "social"
        assert item["platform"] == "Twitter / X"
        assert item["value"] == "twitter.handle"

        db.session.expire_all()
        contact = Contact.query.get(item["id"])
        assert contact.platform == "Twitter / X"

    def test_update_social_contact_platform(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Social Update")
        subject = case.subjects[0]

        created = auth_client.post(
            f"/cms/api/profile/subjects/{subject.id}/contacts",
            json={"contact_type": "social", "platform": "Facebook", "value": "fb.user"},
        )
        assert created.status_code == 201
        cid = created.get_json()["item"]["id"]

        resp = auth_client.put(
            f"/cms/api/profile/subjects/{subject.id}/contacts/{cid}",
            json={"contact_type": "social", "platform": "Instagram", "value": "ig.user"},
        )
        assert resp.status_code == 200
        item = resp.get_json()["item"]
        assert item["platform"] == "Instagram"
        assert item["value"] == "ig.user"

    def test_non_social_contact_platform_stays_none(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Non Social Platform")
        subject = case.subjects[0]

        resp = auth_client.post(
            f"/cms/api/profile/subjects/{subject.id}/contacts",
            json={"contact_type": "email", "platform": "Facebook", "value": "a@b.c"},
        )
        assert resp.status_code == 201
        item = resp.get_json()["item"]
        assert item["contact_type"] == "email"
        assert item["platform"] is None

    def test_viewer_sees_platform_label(self, app, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Social Profile Render")
        subject = case.subjects[0]

        auth_client.post(
            f"/cms/api/profile/subjects/{subject.id}/contacts",
            json={"contact_type": "social", "platform": "LinkedIn", "value": "li.person"},
        )

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "LinkedIn" in html
        assert "li.person" in html
