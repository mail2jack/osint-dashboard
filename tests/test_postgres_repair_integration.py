"""PostgreSQL coverage for the plaintext-repair tool under real FORCE RLS.

The repair script must inventory tenants under a temporary bypass (a
tenantless query with FORCE RLS returns nothing and would look like a
successful no-op) and keep per-tenant work scoped to that tenant's RLS.
"""

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from cms.models import Case, Client, Subject, User, db
from cms.tenant_context import set_tenant_context


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL integration tests require DATABASE_URL=postgresql://...",
)


class TestRepairScriptPostgreSQL:
    def test_dry_run_inventory_works_without_tenant_context(self, app, capsys):
        """A cold run (no tenant context) must still discover plaintext rows:
        the tenant inventory bypasses RLS, then per-tenant work runs under the
        tenant's own RLS context. Dry-run must change nothing."""
        from scripts.repair_encrypted_subject_fields import repair

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        suffix = uuid.uuid4().hex[:8]

        set_tenant_context(db, tenant_a, bypass_rls=True)
        client = Client(tenant_id=tenant_a, name=f"PG repair client {suffix}")
        db.session.add(client)
        db.session.flush()
        case = Case(
            tenant_id=tenant_a,
            case_number=f"PG-REP-{suffix}",
            client_id=client.id,
            title=f"PG repair case {suffix}",
            start_date=date.today(),
            created_by=admin.id,
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(
            name="PG repair person",
            subject_type="person",
            tenant_id=tenant_a,
            email="pg-repair@example.com",
            phone="0611111111",
        )
        subject.encrypt_identifiers()
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.commit()

        # Seed plaintext-at-rest via Core UPDATE (bypasses the ORM guard).
        db.session.execute(
            text("UPDATE subjects SET email = :email, phone = :phone WHERE id = :id"),
            {
                "email": "pg-repair@example.com",
                "phone": "0611111111",
                "id": subject.id,
            },
        )
        db.session.commit()

        # Cold run: no tenant context at all.
        set_tenant_context(db, None)
        db.session.expire_all()

        repair(apply=False, manifest_dir=None)
        captured = capsys.readouterr().out
        assert "WOULD RE-ENCRYPT" in captured
        assert "email" in captured

        # Dry-run must not have re-encrypted anything.
        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        fresh = db.session.get(Subject, subject.id)
        assert fresh.email == "pg-repair@example.com"

        set_tenant_context(db, admin.tenant_id)
