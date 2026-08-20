#!/usr/bin/env python3
"""Create a test subject with all fields populated for verification."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from cms.models import db, Subject, Contact
from cms.tenant_context import set_tenant_context

tenant_id = "3a169c92-04a2-48f9-be1b-1fcf930c0f0f"
test_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

with app.app_context():
    set_tenant_context(db, tenant_id, bypass_rls=True)

    user_id = db.session.execute(db.text("SELECT id FROM users LIMIT 1")).scalar()

    existing = db.session.get(Subject, test_id)
    if existing:
        print(f"Subject already exists: {existing.name}")
    else:
        s = Subject(
            id=test_id,
            tenant_id=tenant_id,
            subject_type="person",
            name="Test, Jan Peter van der",
            achternaam="Test",
            voornamen="Jan Peter",
            voorletters="J.P.",
            tussenvoegsels="van der",
            geslacht="man",
            date_of_birth="1990-05-15",
            place_of_birth="Amsterdam",
            nationality="Dutch",
            bsn_number="123456789",
            identification_number="AB1234567",
            reisdocument_type="paspoort",
            reisdocument_nummer="PA9988776",
            street="Keizersgracht",
            house_number="123",
            house_number_addition="A",
            postal_code="1015 CJ",
            city="Amsterdam",
            email="jan.test@example.com",
            phone="+31612345678",
            bank_account="NL91ABNA0417164300",
            risk_score=75,
            notes="Dit is een testsubject voor verificatie van alle velden.",
            created_by=user_id,
            workflow_social_accounts=["@jan_test", "@peter_test"],
        )
        s.encrypt_identifiers()
        db.session.add(s)
        db.session.flush()
        print(f"Created subject: {s.name} (id={s.id})")

        c_email = Contact(
            id="aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa",
            tenant_id=tenant_id,
            subject_id=test_id,
            contact_type="email",
            value="jan.test@example.com",
            is_primary=True,
            source="test",
        )
        c_email.encrypt_fields()
        db.session.add(c_email)

        c_phone = Contact(
            id="aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa",
            tenant_id=tenant_id,
            subject_id=test_id,
            contact_type="phone",
            value="+31612345678",
            is_primary=False,
            source="test",
        )
        c_phone.encrypt_fields()
        db.session.add(c_phone)
        print("Added 2 contacts (email + phone)")

    # Link to case
    case_id = "82d071da-8af9-487d-8c9d-1f50fa89ca5d"
    exists = db.session.execute(
        db.text("SELECT 1 FROM case_subjects WHERE case_id=:cid AND subject_id=:sid"),
        {"cid": case_id, "sid": test_id},
    ).fetchone()
    if not exists:
        db.session.execute(
            db.text(
                "INSERT INTO case_subjects (case_id, subject_id, role_in_case, status) VALUES (:cid, :sid, 'subject', 'active')"
            ),
            {"cid": case_id, "sid": test_id},
        )
        print(f"Linked to case {case_id[:8]}...")

    db.session.commit()
    print("Done!")
