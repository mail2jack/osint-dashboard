"""
Tests for scripts/repair_encrypted_subject_fields.py (separate PR).

The repair tool ships independently from the live P0 security fix: it is
dry-run by default, never logs values, refuses ``--apply`` without an existing
``--backup-path``, and re-encrypts only *recognizable plaintext* (corrupt
``gAAAA`` Fernet tokens are left untouched).
"""

from datetime import datetime, timezone

import pytest

from cms.models import db, Case, Client, Subject
from cms.services.subject_service import encryptor


def _orm_case(title="Repair Case", tenant_id=None):
    client = Client(name="Repair Client", tenant_id=tenant_id)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"REP-{title.replace(' ', '').upper()}-{client.id[:6]}",
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


def _orm_subject(case, name="Repair Person"):
    subject = Subject(
        name=name,
        subject_type="person",
        tenant_id=case.tenant_id,
        email="repair@example.com",
        phone="0611111111",
    )
    subject.encrypt_identifiers()
    db.session.add(subject)
    db.session.flush()
    case.subjects.append(subject)
    db.session.flush()
    return subject


class TestRepairScript:
    def test_field_state_classification(self, app, db_session):
        from scripts.repair_encrypted_subject_fields import _field_state

        assert _field_state(None) == "empty"
        assert _field_state("") == "empty"
        assert _field_state("plain@example.com") == "plaintext"
        assert _field_state("gAAAAzYX-Corrupt-Not-Fernet") == "unrecognized"
        cipher = encryptor.encrypt("repair@example.com")
        assert _field_state(cipher) == "ciphertext"

    def test_apply_requires_backup(self, app, db_session):
        from scripts.repair_encrypted_subject_fields import repair

        with pytest.raises(SystemExit, match="backup-path"):
            repair(apply=True, manifest_dir=None, backup_path=None)

        with pytest.raises(SystemExit, match="backup-path"):
            repair(
                apply=True,
                manifest_dir=None,
                backup_path="/nonexistent/backup/path",
            )

    def test_dry_run_writes_nothing(self, app, db_session):
        from sqlalchemy import update
        from scripts.repair_encrypted_subject_fields import repair

        case = _orm_case("Repair Dry")
        subject = _orm_subject(case)
        db.session.commit()

        # Simulate a legacy plaintext-at-rest row using a Core-level UPDATE,
        # which bypasses the ORM flush-time re-encryption guard.
        db.session.execute(
            update(Subject)
            .where(Subject.id == subject.id)
            .values(email="repair@example.com", phone="0611111111")
        )
        db.session.commit()

        repair(apply=False, manifest_dir=None)
        db.session.expire_all()
        fresh = db.session.get(Subject, subject.id)
        # Dry-run must not persist the re-encryption.
        assert fresh.email == "repair@example.com"

    def test_apply_reencrypts_only_plaintext(self, app, db_session, tmp_path):
        from sqlalchemy import update
        from scripts.repair_encrypted_subject_fields import repair

        case = _orm_case("Repair Apply")
        subject = _orm_subject(case)
        db.session.commit()

        # Seed plaintext-at-rest (bypasses the ORM flush-time guard).
        db.session.execute(
            update(Subject)
            .where(Subject.id == subject.id)
            .values(email="repair@example.com", phone="0611111111")
        )
        db.session.commit()

        repair(
            apply=True,
            manifest_dir=str(tmp_path),
            backup_path=str(tmp_path),
        )

        db.session.expire_all()
        fresh = db.session.get(Subject, subject.id)
        assert encryptor.decrypt(fresh.email) == "repair@example.com"
        assert encryptor.decrypt(fresh.phone) == "0611111111"
        assert fresh.email != "repair@example.com"
        assert (tmp_path / "affected").exists() or any(tmp_path.iterdir())
