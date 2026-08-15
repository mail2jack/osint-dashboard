"""
Security regression tests — encryption at rest and tenant isolation (P0).

Covers the plaintext-at-rest findings:
  1. encrypt_identifiers() must be idempotent (never double-encrypt).
  2. The workflow case edit POST must not persist decrypted subject fields.
  3. run_action() must re-encrypt identifiers it decrypted and scope created
     rows (findings/screenshots/social accounts) to the action's tenant.
  4. _fill_tenant_id must fail closed instead of attributing rows to the
     first admin in the DB.
"""

import json
from datetime import datetime, timezone

import pytest

from cms.models import (
    db,
    Case,
    Client,
    Finding,
    Subject,
    User,
    ResearchAction,
)
from cms.services.subject_service import encryptor
from cms.workflow.actions.registry import register_action, run_action, ACTION_REGISTRY

ENCRYPTED_SUBJECT_FIELDS = [
    "email",
    "phone",
    "bsn_number",
    "nationality",
    "house_number",
    "postal_code",
]


def _orm_case(title="Enc Case", tenant_id=None):
    client = Client(name="Enc Client", tenant_id=tenant_id)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"ENC-{title.replace(' ', '').upper()}-{client.id[:6]}",
        client_id=client.id,
        title=title,
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
        tenant_id=tenant_id or client.tenant_id,
    )
    db.session.add(case)
    db.session.flush()
    return case


def _orm_subject(case, name="Enc Person"):
    subject = Subject(
        name=name,
        subject_type="person",
        tenant_id=case.tenant_id,
        email="enc@example.com",
        phone="0612345678",
        bsn_number="123456782",
        nationality="Nederlandse",
        house_number="12",
        postal_code="1234AB",
    )
    subject.encrypt_identifiers()
    db.session.add(subject)
    db.session.flush()
    case.subjects.append(subject)
    db.session.flush()
    return subject


def _assert_ciphertext_at_rest(subject):
    """Reload the row raw from the DB and verify every encrypted field is
    ciphertext (decrypts successfully, differs from any plaintext round-trip)."""
    db.session.expire_all()
    fresh = db.session.get(Subject, subject.id)
    for field in ENCRYPTED_SUBJECT_FIELDS:
        value = getattr(fresh, field)
        assert value, f"{field} should still be populated after reload"
        assert encryptor.decrypt(value) is not None, f"{field} is not decryptable"
        assert value != "enc@example.com", f"{field} stored plaintext!"
        assert not value.startswith("enc@"), f"{field} leaked plaintext!"


class TestEncryptIdempotent:
    def test_double_encrypt_keeps_single_ciphertext(self, app, db_session):
        subject = Subject(
            name="Idempotent",
            subject_type="person",
            tenant_id=User.query.filter_by(username="admin").first().tenant_id,
            email="enc@example.com",
            phone="0612345678",
        )
        subject.encrypt_identifiers()
        first = subject.email
        subject.encrypt_identifiers()
        assert subject.email == first, "encrypt_identifiers() double-encrypted"
        assert encryptor.decrypt(subject.email) == "enc@example.com"

    def test_encrypt_after_decrypt_restores_ciphertext(self, app, db_session):
        subject = Subject(
            name="Roundtrip",
            subject_type="person",
            tenant_id=User.query.filter_by(username="admin").first().tenant_id,
            email="enc@example.com",
        )
        subject.encrypt_identifiers()
        subject.decrypt_identifiers()
        assert subject.email == "enc@example.com"
        subject.encrypt_identifiers()
        assert encryptor.decrypt(subject.email) == "enc@example.com"


class TestWorkflowEditKeepsEncryptionAtRest:
    def test_case_edit_post_does_not_persist_plaintext(self, app, auth_client):
        case = _orm_case("Edit At Rest")
        subject = _orm_subject(case)
        db.session.commit()

        prefix = f"subj_{subject.id}"
        resp = auth_client.post(
            f"/cms/workflow/case/{case.id}/edit",
            data={
                "case_number": case.case_number,
                "title": case.title,
                "status": case.status,
                "priority": case.priority,
                "description": case.description or "",
                "client_name": "Enc Client",
                "existing_subject_ids": json.dumps([subject.id]),
                "removed_subject_ids": json.dumps([]),
                f"{prefix}_name": "Enc Person",
                f"{prefix}_type": "person",
                f"{prefix}_voornamen": "",
                f"{prefix}_voorletters": "",
                f"{prefix}_tussenvoegsels": "",
                f"{prefix}_geslacht": "",
                f"{prefix}_date_of_birth": "",
                f"{prefix}_place_of_birth": "",
                f"{prefix}_nationality": "Nederlandse",
                f"{prefix}_bsn_number": "123456782",
                f"{prefix}_reisdocument_type": "",
                f"{prefix}_reisdocument_nummer": "",
                f"{prefix}_postal_code": "1234AB",
                f"{prefix}_house_number": "12",
                f"{prefix}_street": "",
                f"{prefix}_city": "",
                f"{prefix}_bank_account": "",
                f"{prefix}_notes": "",
                f"{prefix}_risk_score": "0",
                f"{prefix}_email": "enc@example.com",
                f"{prefix}_phone": "0612345678",
                f"{prefix}_social_accounts": "",
                f"{prefix}_addresses_data": "",
                f"{prefix}_contacts_data": "",
            },
        )
        assert resp.status_code in (200, 302)

        _assert_ciphertext_at_rest(subject)


class TestReadViewsNeverPersistPlaintext:
    """GET views decrypt in place and the ``audit_read`` decorator commits
    afterward; the flush-time re-encryption guard must keep ciphertext at rest."""

    def test_view_subject_get_keeps_ciphertext_at_rest(self, app, auth_client):
        case = _orm_case("Read View")
        subject = _orm_subject(case)
        db.session.commit()

        resp = auth_client.get(f"/cms/subjects/{subject.id}")
        assert resp.status_code == 200

        _assert_ciphertext_at_rest(subject)

    def test_view_client_get_keeps_ciphertext_at_rest(self, app, auth_client):
        case = _orm_case("Read Client View")
        client = Client(
            name="Read Client",
            tenant_id=case.tenant_id,
            contact_email="client@example.com",
            bsn_number="999999999",
        )
        client.encrypt_naw()
        db.session.add(client)
        db.session.commit()

        resp = auth_client.get(f"/cms/clients/{client.id}")
        assert resp.status_code == 200

        db.session.expire_all()
        fresh = db.session.get(Client, client.id)
        assert fresh.contact_email != "client@example.com"
        assert encryptor.decrypt(fresh.contact_email) == "client@example.com"
        assert encryptor.decrypt(fresh.bsn_number) == "999999999"


class TestReadViewsRenderDecrypted:
    """Legacy GET views decrypt in place for display. Template relationship
    re-queries (``subject.addresses``/``subject.contacts``) trigger autoflush;
    the render paths must run with autoflush off so the guard does not
    re-encrypt mid-render — the HTML must show plaintext while ciphertext stays
    at rest."""

    def test_view_subject_html_is_decrypted(self, app, auth_client):
        case = _orm_case("Render View")
        subject = _orm_subject(case)
        db.session.commit()

        resp = auth_client.get(f"/cms/subjects/{subject.id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "enc@example.com" in body
        assert "gAAAA" not in body

        _assert_ciphertext_at_rest(subject)

    def test_edit_subject_html_is_decrypted(self, app, auth_client):
        case = _orm_case("Render Edit")
        subject = _orm_subject(case)
        db.session.commit()

        resp = auth_client.get(f"/cms/subjects/{subject.id}/edit")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Nederlandse" in body
        assert "123456782" in body
        assert "gAAAA" not in body

        _assert_ciphertext_at_rest(subject)


class TestRunActionReencryptsAndScopesTenant:
    def test_run_action_keeps_subjects_encrypted_and_scopes_findings(
        self, app, db_session
    ):
        def _probe_handler(action):
            case = db.session.get(Case, action.case_id)
            for s in case.subjects:
                s.decrypt_identifiers()
            target = case.subjects[0] if case.subjects else None
            return [
                {
                    "title": "Probe finding",
                    "detail": "probe",
                    "subject_id": target.id if target else None,
                    "screenshots": [{"url": None, "source_url": "https://x.example"}],
                }
            ]

        register_action(
            "test_encryption_probe",
            "Probe",
            "🔒",
            _probe_handler,
            category="open",
        )
        try:
            case = _orm_case("Run At Rest")
            subject = _orm_subject(case)
            admin = User.query.filter_by(username="admin").first()
            action = ResearchAction(
                case_id=case.id,
                tenant_id=case.tenant_id,
                action_type="test_encryption_probe",
                status="pending",
                created_by=admin.id,
            )
            db.session.add(action)
            db.session.commit()

            run_action(action.id)
            db.session.expire_all()

            reloaded = db.session.get(ResearchAction, action.id)
            assert reloaded.status == "completed"

            _assert_ciphertext_at_rest(subject)

            finding = (
                Finding.query.filter_by(case_id=case.id)
                .order_by(Finding.created_at.desc())
                .first()
            )
            assert finding is not None
            assert finding.tenant_id == case.tenant_id
            assert finding.subject_id == subject.id
        finally:
            ACTION_REGISTRY.pop("test_encryption_probe", None)


class TestFillTenantIdFailsClosed:
    def test_insert_without_tenant_context_raises(self, app, db_session):
        from flask import g as _g

        case = _orm_case("Fail Closed")
        admin = User.query.filter_by(username="admin").first()
        db.session.commit()

        saved = _g.get("tenant_id")
        _g.pop("tenant_id", None)
        _g.pop("_cached_tenant_id", None)
        try:
            finding = Finding(
                case_id=case.id,
                created_by=admin.id,
                title="No tenant",
                content="x",
            )
            db.session.add(finding)
            with pytest.raises(ValueError, match="Cannot determine tenant_id"):
                db.session.flush()
            db.session.rollback()
        finally:
            if saved:
                _g.tenant_id = saved
