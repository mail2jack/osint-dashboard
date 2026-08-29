"""
Round-trip regression tests for PR2 (subject_service) — workflow input path.

Covers the inventory gaps from ``docs/subject-model-inventory.md`` §5.1:
5.1.2 (name recomputed server-side), 5.1.3 (org fields on edit),
5.1.6 (achternaam set in workflow), 5.1.7 (vehicle license plate),
5.1.9/5.1.10 (addresses/contacts data), plus the RDW action companion fix.
"""

import json

from cms.models import (
    Case,
    db,
    Subject,
    SocialAccount,
)


def _create_case(auth_client, title, subject_fields, **extra):
    data = {
        "client_name": "RT Client",
        "title": title,
        "subject_0_name": subject_fields.pop("name", "RT Person"),
        "subject_0_type": subject_fields.pop("type", "person"),
        "priority": "medium",
    }
    data.update(subject_fields)
    data.update(extra)
    resp = auth_client.post("/cms/workflow/case/new", data=data)
    assert resp.status_code in (200, 302)
    return Case.query.filter_by(title=title).first()


class TestWorkflowCreateRoundtrip:
    def test_person_sets_achternaam_and_computes_name(self, auth_client):
        case = _create_case(
            auth_client,
            "RT Person",
            {
                "name": "van den Berg",
                "type": "person",
                "subject_0_voornamen": "Jan",
                "subject_0_tussenvoegsels": "van de",
            },
        )
        s = case.subjects[0]
        assert s.achternaam == "van den Berg"
        assert s.voornamen == "Jan"
        assert s.name == "Jan van de van den Berg"

    def test_vehicle_uses_license_plate_field(self, auth_client):
        case = _create_case(
            auth_client,
            "RT Vehicle",
            {
                "name": "",
                "type": "vehicle",
                "subject_0_identification": "AB-12-CD",
                "subject_0_brand": "Ford",
                "subject_0_handelsbenaming": "Focus",
            },
        )
        s = case.subjects[0]
        s.decrypt_identifiers()
        assert s.license_plate == "AB-12-CD"
        assert s.rdw_data["kenteken"] == "AB-12-CD"
        assert s.rdw_data["merk"] == "Ford"
        assert "Ford" in s.name

    def test_online_creates_social_account_rows(self, auth_client, db_session):
        case = _create_case(
            auth_client,
            "RT Online",
            {
                "name": "hacker",
                "type": "online",
                "subject_0_social_accounts": "@hacker, other",
            },
        )
        s = case.subjects[0]
        assert s.subject_type == "online"
        assert s.name == "@hacker"
        accounts = SocialAccount.query.filter_by(subject_id=s.id).all()
        usernames = sorted(a.username for a in accounts)
        assert usernames == ["hacker", "other"]
        assert sorted(s.workflow_social_accounts) == ["@hacker", "@other"]

    def test_person_address_and_contact_roundtrip(self, auth_client):
        addresses = [
            {
                "street": "Hoofdstraat",
                "number": "42",
                "zipcode": "1012AB",
                "town": "Amsterdam",
                "country": "Netherlands",
                "is_primary": True,
            }
        ]
        contacts = [
            {"contact_type": "email", "value": "j@example.com", "is_primary": True}
        ]
        case = _create_case(
            auth_client,
            "RT AddrContact",
            {
                "type": "person",
                "subject_0_addresses_data": json.dumps(addresses),
                "subject_0_contacts_data": json.dumps(contacts),
            },
        )
        s = case.subjects[0]
        s.decrypt_identifiers()
        assert s.street == "Hoofdstraat"
        assert s.house_number == "42"
        assert s.address == "Hoofdstraat 42, 1012AB Amsterdam"
        assert s.email is not None

    def test_person_social_contact_platform_roundtrip(self, auth_client):
        contacts = [
            {
                "contact_type": "social",
                "value": "twitter.handle",
                "platform": "Twitter / X",
                "is_primary": True,
            }
        ]
        case = _create_case(
            auth_client,
            "RT Social Platform",
            {
                "type": "person",
                "subject_0_contacts_data": json.dumps(contacts),
            },
        )
        s = case.subjects[0]
        social = [c for c in s.contacts if c.contact_type == "social"]
        assert len(social) == 1
        assert social[0].platform == "Twitter / X"

    def test_person_social_contact_edit_requires_platform(self, auth_client):
        case = _create_case(
            auth_client,
            "RT Social Edit NoPlatform",
            {
                "type": "person",
                "name": "Doe",
                "subject_0_contacts_data": json.dumps(
                    [
                        {
                            "contact_type": "social",
                            "value": "twitter.handle",
                            "platform": "Twitter / X",
                            "is_primary": True,
                        }
                    ]
                ),
            },
        )
        subject = case.subjects[0]
        resp = auth_client.post(
            f"/cms/workflow/case/{case.id}/edit",
            data={
                "case_number": case.case_number,
                "title": case.title,
                "status": "open",
                "priority": "medium",
                "description": "",
                "existing_subject_ids": json.dumps([subject.id]),
                "removed_subject_ids": "[]",
                "subj_{}_type".format(subject.id): "person",
                "subj_{}_name".format(subject.id): "Doe",
                "subj_{}_contacts_data".format(subject.id): json.dumps(
                    [
                        {"contact_type": "social", "value": "twitter.handle"}
                    ]
                ),
            },
        )
        assert resp.status_code == 302
        db.session.expire_all()
        soc = [
            c
            for c in db.session.get(Subject, subject.id).contacts
            if c.contact_type == "social"
        ]
        assert len(soc) == 1
        assert soc[0].platform == "Twitter / X"


class TestWorkflowEditRoundtrip:
    def _edit(self, auth_client, case, subject, fields):
        data = {
            "case_number": case.case_number,
            "title": case.title,
            "status": "open",
            "priority": "medium",
            "description": "",
            "existing_subject_ids": json.dumps([subject.id]),
            "removed_subject_ids": "[]",
        }
        prefix = f"subj_{subject.id}"
        data[f"{prefix}_type"] = fields.pop("type", subject.subject_type)
        data[f"{prefix}_name"] = fields.pop("name", subject.name)
        data.update(fields)
        return auth_client.post(f"/cms/workflow/case/{case.id}/edit", data=data)

    def test_name_recomputed_from_split_fields(self, auth_client):
        case = _create_case(
            auth_client,
            "RT Edit Person",
            {"type": "person", "name": "Doe"},
        )
        subject = case.subjects[0]
        resp = self._edit(
            auth_client,
            case,
            subject,
            {
                "subj_{}_voornamen".format(subject.id): "Piet",
                "subj_{}_tussenvoegsels".format(subject.id): "van de",
            },
        )
        assert resp.status_code in (200, 302)
        db.session.expire_all()
        subject = db.session.get(Subject, subject.id)
        assert subject.achternaam == "Doe"
        assert subject.voornamen == "Piet"
        assert subject.name == "Piet van de Doe"

    def test_person_edit_no_name_duplication(self, auth_client):
        case = _create_case(
            auth_client,
            "RT No Dup",
            {
                "type": "person",
                "name": "van den Berg",
                "subject_0_voornamen": "Jan",
            },
        )
        subject = case.subjects[0]
        assert subject.name == "Jan van den Berg"
        self._edit(
            auth_client,
            case,
            subject,
            {
                "subj_{}_name".format(subject.id): subject.achternaam,
                "subj_{}_voornamen".format(subject.id): subject.voornamen,
            },
        )
        db.session.expire_all()
        subject = db.session.get(Subject, subject.id)
        assert subject.achternaam == "van den Berg"
        assert subject.name == "Jan van den Berg"

    def test_company_org_fields_persist_on_edit(self, auth_client):
        case = _create_case(
            auth_client,
            "RT Edit Company",
            {"type": "company", "name": "Acme BV"},
        )
        subject = case.subjects[0]
        self._edit(
            auth_client,
            case,
            subject,
            {
                "subj_{}_registration_number".format(subject.id): "30128191",
                "subj_{}_legal_form".format(subject.id): "BV",
                "subj_{}_notes".format(subject.id): "interne notitie",
            },
        )
        db.session.expire_all()
        subject = db.session.get(Subject, subject.id)
        assert subject.registration_number == "30128191"
        assert subject.legal_form == "BV"
        assert subject.notes == "interne notitie"

    def test_vehicle_plate_editable_on_edit(self, auth_client):
        case = _create_case(
            auth_client,
            "RT Edit Vehicle",
            {
                "type": "vehicle",
                "name": "",
                "subject_0_identification": "AA-11-BB",
            },
        )
        subject = case.subjects[0]
        self._edit(
            auth_client,
            case,
            subject,
            {
                "subj_{}_identification".format(subject.id): "CC-22-DD",
                "subj_{}_brand".format(subject.id): "Volkswagen",
            },
        )
        db.session.expire_all()
        subject = db.session.get(Subject, subject.id)
        subject.decrypt_identifiers()
        assert subject.license_plate == "CC-22-DD"
        assert subject.rdw_data["kenteken"] == "CC-22-DD"
        assert subject.rdw_data["merk"] == "Volkswagen"


class TestRdwActionCompanion:
    def test_rdw_plate_prefers_license_plate_over_legacy(self, db_session):
        from cms.workflow.actions.vehicle_action import _rdw_plate

        subject = Subject(
            name="Ford Focus",
            subject_type="vehicle",
            license_plate="AB-12-CD",
            identification_number="X9",
        )
        subject.encrypt_identifiers()
        assert _rdw_plate(subject) == "AB-12-CD"

    def test_rdw_plate_falls_back_to_rdw_data(self, db_session):
        from cms.workflow.actions.vehicle_action import _rdw_plate

        subject = Subject(
            name="Ford Focus",
            subject_type="vehicle",
            rdw_data={"kenteken": "ZZ-99-YY"},
        )
        assert _rdw_plate(subject) == "ZZ-99-YY"

    def test_rdw_plate_none_when_absent(self, db_session):
        from cms.workflow.actions.vehicle_action import _rdw_plate

        subject = Subject(name="Ford Focus", subject_type="vehicle")
        assert _rdw_plate(subject) is None
