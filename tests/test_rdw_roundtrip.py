"""RDW round-trip tests (P1 commit 3).

Covers:
- ``subject_service.edit`` persists the 4 RDW fields the workflow form
  collects (vermogen, bruto_bpm, datum_tenaamstelling,
  openstaande_terugroepactie) plus rdw_type
- cleared RDW fields persist as empty instead of being silently kept
- absent RDW fields are preserved (merge semantics)
- the workflow case-edit form round-trips and clears these fields
"""

import json
import uuid
from datetime import UTC, datetime

from cms.models import (
    Case,
    Client,
    Subject,
    db,
)
from cms.services.subject_service import subject_service


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
        title="RDW Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=owner.id if owner else 1,
    )
    db.session.add(case)
    db.session.flush()
    return case


def _vehicle_subject(**rdw):
    from flask import g

    subj = subject_service.create(
        {
            "subject_type": "vehicle",
            "name": "Test Vehicle",
            "license_plate": "AB-123-K",
            "brand": "Volkswagen",
            "vehicle_type": "personenauto",
        },
        created_by=1,
        tenant_id=g.get("tenant_id"),
    )
    if rdw:
        subj.rdw_data = dict(rdw)
    db.session.add(subj)
    db.session.commit()
    return subj


class TestSubjectServiceRdwEdit:
    def test_edit_persists_workflow_rdw_fields(self):
        subj = _vehicle_subject()
        db.session.commit()

        subject_service.edit(
            subj,
            {
                "vermogen": "120",
                "bruto_bpm": "4500",
                "datum_tenaamstelling": "2020-01-02",
                "openstaande_terugroepactie": "Nee",
                "rdw_type": "personenauto",
                "handelsbenaming": "Golf",
            },
            actor_id=1,
        )
        db.session.commit()
        db.session.refresh(subj)

        rdw = subj.rdw_data or {}
        assert rdw["vermogen"] == "120"
        assert rdw["bruto_bpm"] == "4500"
        assert rdw["datum_tenaamstelling"] == "2020-01-02"
        assert rdw["openstaande_terugroepactie"] == "Nee"
        assert rdw["rdw_type"] == "personenauto"
        assert rdw["handelsbenaming"] == "Golf"

    def test_edit_clears_rdw_field(self):
        subj = _vehicle_subject(
            handelsbenaming="OldModel", vermogen="120", merk="Volkswagen"
        )
        db.session.commit()

        subject_service.edit(
            subj,
            {"handelsbenaming": "", "vermogen": ""},
            actor_id=1,
        )
        db.session.commit()
        db.session.refresh(subj)

        rdw = subj.rdw_data or {}
        assert rdw["handelsbenaming"] == ""
        assert rdw["vermogen"] == ""

    def test_edit_preserves_absent_rdw_fields(self):
        subj = _vehicle_subject(vermogen="120", handelsbenaming="Keep")
        db.session.commit()

        subject_service.edit(
            subj,
            {"handelsbenaming": "Changed"},
            actor_id=1,
        )
        db.session.commit()
        db.session.refresh(subj)

        rdw = subj.rdw_data or {}
        assert rdw["handelsbenaming"] == "Changed"
        assert rdw["vermogen"] == "120"


class TestWorkflowCaseEditRdwRoundtrip:
    def _post_form(self, client, case, subject, rdw_values, base=None):
        from cms.workflow.routes import _VEHICLE_RDW_FIELDS

        sid = subject.id
        form = {
            "case_number": base.get("case_number") if base else case.case_number,
            "title": (base or {}).get("title", "RDW Case"),
            "status": (base or {}).get("status", "open"),
            "priority": (base or {}).get("priority", "medium"),
            "description": (base or {}).get("description", ""),
            "existing_subject_ids": json.dumps([sid]),
            "removed_subject_ids": json.dumps([]),
            f"subj_{sid}_type": "vehicle",
            f"subj_{sid}_name": subject.name,
            f"subj_{sid}_identification": subject.license_plate or "",
            f"subj_{sid}_brand": subject.brand or "",
            f"subj_{sid}_vehicle_type": subject.vehicle_type or "",
            f"subj_{sid}_vin": subject.vin or "",
            f"subj_{sid}_insurance_company": subject.insurance_company or "",
        }
        for field in _VEHICLE_RDW_FIELDS:
            form[f"subj_{sid}_{field}"] = rdw_values.get(field, "")
        return client.post(f"/cms/workflow/case/{case.id}/edit", data=form)

    def test_roundtrips_workflow_rdw_fields(self, auth_client):
        case = _make_case(owner=None)
        subj = _vehicle_subject()
        case.subjects.append(subj)
        db.session.commit()

        resp = self._post_form(
            auth_client,
            case,
            subj,
            {
                "vermogen": "110",
                "bruto_bpm": "3800",
                "datum_tenaamstelling": "2019-05-01",
                "openstaande_terugroepactie": "Geen",
                "rdw_type": "bedrijfsauto",
            },
        )
        assert resp.status_code == 302, resp.status_code
        db.session.expire_all()
        subj = db.session.get(Subject, subj.id)
        rdw = subj.rdw_data or {}
        assert rdw["vermogen"] == "110"
        assert rdw["bruto_bpm"] == "3800"
        assert rdw["datum_tenaamstelling"] == "2019-05-01"
        assert rdw["openstaande_terugroepactie"] == "Geen"
        assert rdw["rdw_type"] == "bedrijfsauto"

    def test_clears_workflow_rdw_field(self, auth_client):
        case = _make_case(owner=None)
        subj = _vehicle_subject(handelsbenaming="OldModel", vermogen="120")
        case.subjects.append(subj)
        db.session.commit()

        resp = self._post_form(
            auth_client,
            case,
            subj,
            {"handelsbenaming": "", "vermogen": ""},
        )
        assert resp.status_code == 302, resp.status_code
        db.session.expire_all()
        subj = db.session.get(Subject, subj.id)
        rdw = subj.rdw_data or {}
        assert rdw["handelsbenaming"] == ""
        assert rdw["vermogen"] == ""
