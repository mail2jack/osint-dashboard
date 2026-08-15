"""
ADR-0001 PR7b — inline add/edit/delete on the tabbed Subject Profile (write
side, behind the ``subject_first_investigations`` feature flag).

Covers the flag gate (off -> 404), base-field PATCH, per-tab CRUD for
identifiers/facts/addresses/contacts/social accounts/relations, provenance
capture, legacy mirroring of primary address/contact, chain-of-custody
protection (deletion blocked for finding-linked rows), and access control.
"""

from cms.models import (
    Address,
    AuditLog,
    Case,
    Contact,
    FeatureFlag,
    SocialAccount,
    Subject,
    SubjectFact,
    SubjectIdentifier,
    User,
    db,
    subject_relations,
)
from cms.services.subject_service import encryptor


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


def _case_with_subject(auth_client, title="Write Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "Write Client",
            "title": title,
            "subject_0_name": "Write Person",
            "subject_0_type": "person",
            "subject_0_email": "write@example.com",
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


def _api(subject_id, path, **kwargs):
    return f"/cms/api/profile/subjects/{subject_id}{path}"


class TestFlagGate:
    def test_all_write_apis_404_when_flag_off(self, auth_client):
        case = _case_with_subject(auth_client, title="Write Gate Off")
        subject = case.subjects[0]
        sid = subject.id

        resp = auth_client.patch(_api(sid, ""), json={"name": "X"})
        assert resp.status_code == 404, resp.status_code

        for url, payload in [
            (_api(sid, "/identifiers"), {"identifier_type": "email", "value": "a@b.c"}),
            (_api(sid, "/facts"), {"fact_key": "k", "value": "v"}),
            (_api(sid, "/addresses"), {"street": "X"}),
            (_api(sid, "/contacts"), {"contact_type": "email", "value": "a@b.c"}),
            (_api(sid, "/social-accounts"), {"platform": "x", "username": "u"}),
            (_api(sid, "/relations"), {"related_subject_id": "other"}),
        ]:
            resp = auth_client.post(url, json=payload)
            assert resp.status_code == 404, f"{url} -> {resp.status_code}"


class TestBaseFields:
    def test_patch_updates_base_fields_with_audit(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Base")
        subject = case.subjects[0]

        resp = auth_client.patch(
            _api(subject.id, ""),
            json={
                "name": "Renamed Subject",
                "risk_score": "45",
                "risk_factors": "tax_evasion, shell_company",
                "notes": "Some notes here",
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["message"] == "Subject updated"
        assert "risk_factors" in data["changes"]

        db.session.refresh(subject)
        assert subject.name == "Renamed Subject"
        assert subject.risk_score == 45
        assert subject.risk_factors == ["tax_evasion", "shell_company"]
        assert subject.notes == "Some notes here"

        audit = AuditLog.query.filter_by(
            entity_type="subject", entity_id=str(subject.id), action="update"
        ).first()
        assert audit is not None
        assert audit.user_id == admin.id

    def test_patch_person_name_recomputed_from_splits(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Splits")
        subject = case.subjects[0]

        resp = auth_client.patch(
            _api(subject.id, ""),
            json={
                "achternaam": "Doe",
                "voornamen": "Jane",
                "voorletters": "J",
                "geslacht": "vrouw",
                "nationality": "Dutch",
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        db.session.refresh(subject)
        assert subject.achternaam == "Doe"
        assert subject.voornamen == "Jane"
        assert "Jane" in subject.name and "Doe" in subject.name


class TestIdentifierCrud:
    def test_create_update_delete(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Ident")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/identifiers"),
            json={
                "identifier_type": "email",
                "value": "ident@example.com",
                "status": "verified",
                "source": "kvk register",
                "reliability": "high",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        item = resp.get_json()["item"]
        assert item["value"] == "ident@example.com"

        ident = SubjectIdentifier.query.get(item["id"])
        assert ident is not None
        assert ident.get_value() == "ident@example.com"
        assert ident.status == "verified"
        assert ident.created_by == admin.id
        assert ident.value_enc != "ident@example.com"  # encrypted at rest
        assert ident.fingerprint_keyed is not None

        resp = auth_client.put(
            _api(subject.id, f"/identifiers/{item['id']}"),
            json={
                "identifier_type": "email",
                "value": "new@example.com",
                "status": "rejected",
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        db.session.refresh(ident)
        assert ident.get_value() == "new@example.com"
        assert ident.status == "rejected"

        resp = auth_client.delete(_api(subject.id, f"/identifiers/{item['id']}"))
        assert resp.status_code == 200
        assert SubjectIdentifier.query.get(item["id"]) is None

    def test_delete_identifier_linked_to_finding_blocked(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Ident Locked")
        subject = case.subjects[0]

        ident = SubjectIdentifier(
            subject_id=subject.id,
            tenant_id=subject.tenant_id,
            identifier_type="bsn",
            status="candidate",
            created_by=admin.id,
            finding_id="some-finding",
        )
        ident.set_value("123456789")
        db.session.add(ident)
        db.session.commit()

        resp = auth_client.delete(_api(subject.id, f"/identifiers/{ident.id}"))
        assert resp.status_code == 400
        assert "finding" in resp.get_json()["error"]
        assert SubjectIdentifier.query.get(ident.id) is not None


class TestFactCrud:
    def test_create_update_delete_with_verification(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Fact")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/facts"),
            json={
                "fact_key": "income",
                "value": "4000 EUR",
                "status": "candidate",
                "source": "interview",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        item = resp.get_json()["item"]
        fact = SubjectFact.query.get(item["id"])
        assert fact is not None
        assert fact.get_value() == "4000 EUR"

        resp = auth_client.put(
            _api(subject.id, f"/facts/{item['id']}"),
            json={"fact_key": "income", "value": "4500 EUR", "status": "verified"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        db.session.refresh(fact)
        assert fact.get_value() == "4500 EUR"
        assert fact.verified_by == admin.id
        assert fact.verified_at is not None

        resp = auth_client.delete(_api(subject.id, f"/facts/{item['id']}"))
        assert resp.status_code == 200
        assert SubjectFact.query.get(item["id"]) is None


class TestAddressCrud:
    def test_create_primary_mirrors_to_subject(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Address")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/addresses"),
            json={
                "street": "Teststraat",
                "number": "12",
                "zipcode": "1234AB",
                "town": "Utrecht",
                "country": "Netherlands",
                "is_primary": True,
                "source": "BRP",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        item = resp.get_json()["item"]
        assert item["street"] == "Teststraat"

        addr = Address.query.get(item["id"])
        assert addr is not None
        assert addr.to_dict(decrypted=True)["street"] == "Teststraat"
        db.session.refresh(subject)
        assert "Teststraat 12" in subject.address

    def test_update_switches_primary(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Address 2")
        subject = case.subjects[0]

        r1 = auth_client.post(
            _api(subject.id, "/addresses"),
            json={"street": "Eerste", "number": "1", "is_primary": True},
        )
        r2 = auth_client.post(
            _api(subject.id, "/addresses"),
            json={"street": "Tweede", "number": "2", "is_primary": False},
        )
        assert r1.status_code == 201 and r2.status_code == 201
        second_id = r2.get_json()["item"]["id"]

        resp = auth_client.put(
            _api(subject.id, f"/addresses/{second_id}"),
            json={"street": "Tweede", "number": "2", "is_primary": True},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

        first = Address.query.get(r1.get_json()["item"]["id"])
        second = Address.query.get(second_id)
        assert first.is_primary is False
        assert second.is_primary is True

        db.session.refresh(subject)
        assert "Tweede 2" in subject.address

    def test_delete_address(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Address 3")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/addresses"), json={"street": "Deleteweg", "number": "3"}
        )
        assert resp.status_code == 201
        addr_id = resp.get_json()["item"]["id"]

        resp = auth_client.delete(_api(subject.id, f"/addresses/{addr_id}"))
        assert resp.status_code == 200
        assert Address.query.get(addr_id) is None


class TestContactCrud:
    def test_create_primary_mirrors_to_subject(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Contact")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/contacts"),
            json={
                "contact_type": "email",
                "value": "primary@example.com",
                "is_primary": True,
                "source": "website",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        item = resp.get_json()["item"]
        contact = Contact.query.get(item["id"])
        assert contact is not None

        db.session.refresh(subject)
        assert encryptor.decrypt(subject.email) == "primary@example.com"

        resp = auth_client.post(
            _api(subject.id, "/contacts"),
            json={
                "contact_type": "phone",
                "value": "+31 6 12345678",
                "is_primary": True,
            },
        )
        assert resp.status_code == 201
        db.session.refresh(subject)
        assert "612345678" in encryptor.decrypt(subject.phone)

        resp = auth_client.delete(_api(subject.id, f"/contacts/{item['id']}"))
        assert resp.status_code == 200
        assert Contact.query.get(item["id"]) is None


class TestSocialCrud:
    def test_create_update_delete(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Social")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/social-accounts"),
            json={
                "platform": "Twitter",
                "username": "some_user",
                "url": "https://twitter.com/some_user",
                "source": "osint",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        item = resp.get_json()["item"]
        assert item["platform"] == "twitter"

        account = SocialAccount.query.get(item["id"])
        assert account is not None
        assert account.username == "some_user"
        assert account.updated_by == admin.id

        resp = auth_client.put(
            _api(subject.id, f"/social-accounts/{item['id']}"),
            json={"platform": "twitter", "username": "renamed_user"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        db.session.refresh(account)
        assert account.username == "renamed_user"

        resp = auth_client.delete(_api(subject.id, f"/social-accounts/{item['id']}"))
        assert resp.status_code == 200
        assert SocialAccount.query.get(item["id"]) is None


class TestRelationCrud:
    def test_create_upsert_delete(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Relation")
        subject = case.subjects[0]

        other = Subject(
            name="Relation Other",
            subject_type="person",
            tenant_id=subject.tenant_id,
            created_by=admin.id,
        )
        db.session.add(other)
        db.session.commit()

        resp = auth_client.post(
            _api(subject.id, "/relations"),
            json={
                "related_subject_id": str(other.id),
                "relation_type": "family",
                "direction": "outgoing",
                "source": "case file",
                "reliability": "high",
                "status": "verified",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["pair"] is not None

        row = db.session.execute(
            subject_relations.select().where(
                db.or_(
                    (subject_relations.c.subject_id == subject.id)
                    & (subject_relations.c.related_subject_id == other.id),
                    (subject_relations.c.subject_id == other.id)
                    & (subject_relations.c.related_subject_id == subject.id),
                )
            )
        ).first()
        assert row is not None
        assert row.relation_type == "family"

        # Same pair re-saved -> updated, still one row.
        resp = auth_client.post(
            _api(subject.id, "/relations"),
            json={
                "related_subject_id": str(other.id),
                "relation_type": "business",
                "direction": "mutual",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        rows = db.session.execute(
            subject_relations.select().where(
                db.or_(
                    (subject_relations.c.subject_id == subject.id)
                    & (subject_relations.c.related_subject_id == other.id),
                    (subject_relations.c.subject_id == other.id)
                    & (subject_relations.c.related_subject_id == subject.id),
                )
            )
        ).fetchall()
        assert len(rows) == 1
        assert rows[0].relation_type == "business"

        resp = auth_client.delete(_api(subject.id, f"/relations/{other.id}"))
        assert resp.status_code == 200
        remaining = db.session.execute(
            subject_relations.select().where(
                db.or_(
                    (subject_relations.c.subject_id == subject.id)
                    & (subject_relations.c.related_subject_id == other.id),
                    (subject_relations.c.subject_id == other.id)
                    & (subject_relations.c.related_subject_id == subject.id),
                )
            )
        ).fetchall()
        assert remaining == []

    def test_self_relation_rejected(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Self Relation")
        subject = case.subjects[0]

        resp = auth_client.post(
            _api(subject.id, "/relations"),
            json={"related_subject_id": str(subject.id), "relation_type": "family"},
        )
        assert resp.status_code == 400
        assert "itself" in resp.get_json()["error"]


class TestAccessControl:
    def test_viewer_cannot_write(self, app, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write Viewer")
        subject = case.subjects[0]

        viewer = _make_user("write_viewer", "viewer", admin.tenant_id)
        viewer_client = app.test_client()
        with viewer_client.session_transaction() as sess:
            sess["_user_id"] = str(viewer.id)
            sess["_fresh"] = True
            sess["_remember"] = "set"

        resp = viewer_client.post(
            _api(subject.id, "/identifiers"),
            json={"identifier_type": "email", "value": "x@y.z"},
        )
        assert resp.status_code == 403

    def test_investigator_without_case_access_denied(self, app, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Write No Access")
        subject = case.subjects[0]

        analyst = _make_user("write_analyst", "investigator", admin.tenant_id)
        analyst_client = app.test_client()
        with analyst_client.session_transaction() as sess:
            sess["_user_id"] = str(analyst.id)
            sess["_fresh"] = True
            sess["_remember"] = "set"

        resp = analyst_client.post(
            _api(subject.id, "/identifiers"),
            json={"identifier_type": "email", "value": "x@y.z"},
        )
        assert resp.status_code == 403
