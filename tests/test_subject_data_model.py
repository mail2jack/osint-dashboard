"""Tests for the ADR-0001 PR3 data model: subject_identifiers, subject_facts,
provenance columns on addresses/contacts/social_accounts, typed/directed
subject_relations, case_subjects role/status/note, and D3 fingerprints."""

import pytest
from sqlalchemy import inspect, text

from cms.fingerprint_utils import (
    FingerprintError,
    Fingerprinter,
    fingerprint,
    fingerprint_identifier,
    normalize_identifier,
)
from cms.models import (
    Address,
    Case,
    Client,
    Contact,
    SocialAccount,
    Subject,
    SubjectFact,
    SubjectIdentifier,
    Tenant,
    db,
    case_subjects,
    subject_relations,
)


def _create_subject(name="Test Subject", subject_type="person"):
    admin = db.session.execute(
        text("SELECT tenant_id FROM users WHERE username = 'admin'")
    ).scalar()
    subject = Subject(
        tenant_id=admin, subject_type=subject_type, name=name, achternaam=name
    )
    db.session.add(subject)
    db.session.flush()
    return subject


def _tables():
    return set(inspect(db.engine).get_table_names())


class TestDataModelTables:
    def test_subject_identifiers_table_exists(self):
        assert "subject_identifiers" in _tables()

    def test_subject_facts_table_exists(self):
        assert "subject_facts" in _tables()

    def test_columns_created(self):
        inspector = inspect(db.engine)
        id_cols = {c["name"] for c in inspector.get_columns("subject_identifiers")}
        assert {
            "id",
            "subject_id",
            "tenant_id",
            "identifier_type",
            "value_enc",
            "fingerprint_keyed",
            "status",
            "source",
            "source_url",
            "observed_at",
            "reliability",
            "action_id",
            "finding_id",
            "created_by",
            "created_at",
            "updated_at",
        } <= id_cols

        fact_cols = {c["name"] for c in inspector.get_columns("subject_facts")}
        assert {
            "fact_key",
            "value_enc",
            "verified_by",
            "verified_at",
        } <= fact_cols

        for table in ("addresses", "contacts", "social_accounts"):
            cols = {c["name"] for c in inspector.get_columns(table)}
            assert {"source", "status", "observed_at", "action_id"} <= cols
            if table != "social_accounts":
                assert "finding_id" in cols
            assert "updated_by" in cols

        rel_cols = {c["name"] for c in inspector.get_columns("subject_relations")}
        assert {
            "relation_type",
            "direction",
            "source",
            "reliability",
            "status",
            "observed_at",
            "case_number",
            "created_by",
        } <= rel_cols
        assert "relationship_type" not in rel_cols

        case_subject_cols = {c["name"] for c in inspector.get_columns("case_subjects")}
        assert {"role_in_case", "status", "note"} <= case_subject_cols


class TestSubjectIdentifier:
    def test_roundtrip_encrypts_and_fingerprints(self):
        subject = _create_subject()
        identifier = SubjectIdentifier(
            subject_id=subject.id, tenant_id=subject.tenant_id, identifier_type="email"
        )
        identifier.set_value("  Jane.Doe@Example.COM  ")
        db.session.add(identifier)
        db.session.commit()

        fresh = db.session.get(SubjectIdentifier, identifier.id)
        assert fresh.get_value() == "  Jane.Doe@Example.COM  "
        assert fresh.value_enc != "  Jane.Doe@Example.COM  "
        assert fresh.fingerprint_keyed == fingerprint_identifier(
            "email", "  Jane.Doe@Example.COM  "
        )
        assert fresh.status == "candidate"
        assert fresh.tenant_id == subject.tenant_id

    def test_auto_fill_tenant_id(self):
        subject = _create_subject()
        identifier = SubjectIdentifier(subject_id=subject.id, identifier_type="phone")
        identifier.set_value("+31612345678")
        db.session.add(identifier)
        db.session.commit()
        assert identifier.tenant_id == subject.tenant_id

    def test_subject_backref(self):
        subject = _create_subject()
        identifier = SubjectIdentifier(
            subject_id=subject.id, tenant_id=subject.tenant_id, identifier_type="iban"
        )
        identifier.set_value("NL91 ABNA 0417 1643 00")
        db.session.add(identifier)
        db.session.commit()
        assert [i.id for i in subject.identifiers] == [identifier.id]


class TestSubjectFact:
    def test_roundtrip(self):
        subject = _create_subject()
        fact = SubjectFact(
            subject_id=subject.id,
            tenant_id=subject.tenant_id,
            fact_key="employer",
            source="manual",
            status="verified",
        )
        fact.set_value("Acme BV")
        db.session.add(fact)
        db.session.commit()

        fresh = db.session.get(SubjectFact, fact.id)
        assert fresh.get_value() == "Acme BV"
        assert fresh.value_enc != "Acme BV"
        assert fresh.status == "verified"
        assert fresh.source == "manual"


class TestProvenanceColumns:
    def test_address_provenance_roundtrip(self):
        subject = _create_subject()
        address = Address(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            street="Main St",
            number="1",
            zipcode="1000 AB",
            town="Amsterdam",
            country="NL",
            source="subject_form",
            status="verified",
            observed_at=None,
        )
        address.encrypt_fields()
        db.session.add(address)
        db.session.commit()

        fresh = db.session.get(Address, address.id)
        fresh.decrypt_fields()
        assert fresh.street == "Main St"
        assert fresh.source == "subject_form"
        assert fresh.status == "verified"

    def test_contact_provenance_roundtrip(self):
        subject = _create_subject()
        contact = Contact(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            contact_type="email",
            value="a@b.nl",
            source="workflow",
            status="candidate",
        )
        contact.encrypt_fields()
        db.session.add(contact)
        db.session.commit()

        fresh = db.session.get(Contact, contact.id)
        fresh.decrypt_fields()
        assert fresh.value == "a@b.nl"
        assert fresh.source == "workflow"
        assert fresh.status == "candidate"

    def test_social_account_provenance_roundtrip(self):
        subject = _create_subject()
        account = SocialAccount(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            platform="linkedin",
            username="janedoe",
            source="manual",
            status="verified",
        )
        db.session.add(account)
        db.session.commit()

        fresh = db.session.get(SocialAccount, account.id)
        assert fresh.username == "janedoe"
        assert fresh.source == "manual"
        assert fresh.status == "verified"


class TestSubjectRelations:
    def _add_via_api(self, auth_client, subj_a, subj_b, rel_type="family"):
        return auth_client.post(
            f"/cms/subjects/{subj_a}/add-relationship",
            json={"related_subject_id": subj_b, "relationship_type": rel_type},
        )

    def test_single_row_storage(self, auth_client):
        a = _create_subject("A")
        b = _create_subject("B")
        db.session.commit()
        resp = self._add_via_api(auth_client, a.id, b.id, "family")
        assert resp.status_code == 200
        assert resp.get_json()["relationship"]["bidirectional"] is True

        rows = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == a.id)
                | (subject_relations.c.related_subject_id == a.id)
            )
        ).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert {row.subject_id, row.related_subject_id} == {a.id, b.id}
        assert row.direction == "mutual"
        assert row.relation_type == "family"

    def test_type_mapping(self, auth_client):
        a = _create_subject("A")
        b = _create_subject("B")
        c = _create_subject("C")
        db.session.commit()
        resp = self._add_via_api(auth_client, a.id, b.id, "business_partner")
        assert resp.status_code == 200
        row = db.session.execute(subject_relations.select()).fetchone()
        assert row.relation_type == "business"

        resp = self._add_via_api(auth_client, a.id, c.id, "colleague")
        assert resp.status_code == 200
        rows = db.session.execute(subject_relations.select()).fetchall()
        assert sorted(r.relation_type for r in rows) == ["business", "business"]

    def test_get_shows_edge_from_either_side(self, auth_client):
        a = _create_subject("Alice")
        b = _create_subject("Bob")
        db.session.commit()
        self._add_via_api(auth_client, a.id, b.id, "family")

        for subj_id in (a.id, b.id):
            resp = auth_client.get(f"/cms/subjects/{subj_id}/relationships")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["nodes"]) == 2
            assert len(data["edges"]) == 1
            assert data["edges"][0]["type"] == "family"

    def test_remove_relationship_both_sides(self, auth_client):
        a = _create_subject("A")
        b = _create_subject("B")
        db.session.commit()
        self._add_via_api(auth_client, a.id, b.id, "family")
        resp = auth_client.post(
            f"/cms/subjects/{b.id}/remove-relationship",
            json={"related_subject_id": a.id},
        )
        assert resp.status_code == 200
        assert db.session.execute(subject_relations.select()).fetchall() == []

    def test_cross_tenant_rejected(self, auth_client):
        a = _create_subject("Tenant A subject")
        db.session.commit()

        tenant_b = Tenant(
            name="Tenant B",
            slug="tenant-b",
            is_active=True,
            tier="enterprise",
            join_code="tenant-b-code",
        )
        db.session.add(tenant_b)
        db.session.flush()
        b = Subject(
            tenant_id=tenant_b.id, subject_type="person", name="Tenant B subject"
        )
        db.session.add(b)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/subjects/{a.id}/add-relationship",
            json={"related_subject_id": b.id},
        )
        assert resp.status_code == 404

    def test_related_subjects_relationship_both_directions(self):
        a = _create_subject("Alice")
        b = _create_subject("Bob")
        db.session.add_all([a, b])
        db.session.flush()
        canonical_a, canonical_b = sorted([a.id, b.id])
        db.session.execute(
            subject_relations.insert().values(
                subject_id=canonical_a,
                related_subject_id=canonical_b,
                relation_type="family",
                direction="mutual",
            )
        )
        db.session.commit()
        db.session.expire_all()

        assert {s.id for s in a.related_subjects} == {b.id}
        assert {s.id for s in b.related_subjects} == {a.id}


class TestCaseSubjectsColumns:
    def test_role_status_note_roundtrip(self):
        from datetime import date

        subject = _create_subject()
        client = Client(tenant_id=subject.tenant_id, name="Case client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            tenant_id=subject.tenant_id,
            case_number="2000-00001",
            client_id=client.id,
            title="Test case",
            start_date=date.today(),
            created_by=None,
        )
        db.session.add(case)
        db.session.flush()

        db.session.execute(
            case_subjects.insert().values(
                case_id=case.id,
                subject_id=subject.id,
                role_in_case="witness",
                status="active",
                note="primary contact",
            )
        )
        db.session.commit()

        row = db.session.execute(
            case_subjects.select().where(case_subjects.c.case_id == case.id)
        ).fetchone()
        assert row.role_in_case == "witness"
        assert row.status == "active"
        assert row.note == "primary contact"


class TestFingerprintUtils:
    def test_deterministic(self):
        assert fingerprint("abc") == fingerprint("abc")
        assert fingerprint("abc") != fingerprint("abd")

    def test_normalization(self):
        assert normalize_identifier("email", "  A.B@Example.COM ") == "a.b@example.com"
        assert normalize_identifier("phone", "+31 6-1234.5678") == "+31612345678"
        assert (
            normalize_identifier("iban", "NL91 ABNA 0417 1643 00")
            == "nl91abna0417164300"
        )
        assert normalize_identifier("platform_handle", "@JanDoe") == "jandoe"
        assert fingerprint_identifier("email", "A@B.NL") == fingerprint_identifier(
            "email", "a@b.nl"
        )

    def test_keyed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CMS_FINGERPRINT_KEY", "k" * 64)
        Fingerprinter.reset()
        first = fingerprint("value")
        monkeypatch.setenv("CMS_FINGERPRINT_KEY", "z" * 64)
        Fingerprinter.reset()
        second = fingerprint("value")
        assert first != second
        Fingerprinter.reset()

    def test_production_requires_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CMS_FINGERPRINT_KEY", raising=False)
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setattr(
            Fingerprinter, "_key_file_path", lambda self: str(tmp_path / "no-key")
        )
        Fingerprinter.reset()
        with pytest.raises(FingerprintError):
            fingerprint("value")
        Fingerprinter.reset()
