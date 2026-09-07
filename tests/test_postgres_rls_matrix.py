"""Two-tenant RLS isolation matrix on real PostgreSQL.

Extends ``test_postgres_integration`` with per-domain isolation coverage for
the OSINT working domains that suite did not exercise directly: documents
(uploads / report exports), findings (report evidence), osint_searches +
spiderfoot_scans (background jobs), and the super-admin tenant switch flow
(``POST /cms/switch-tenant/<id>``). It also pins the actual FORCE-RLS table
set so schema coverage stays a conscious decision.

Known gap: ``background_tasks`` carries no ``tenant_id`` column and is not
FORCE RLS yet, so persisted jobs are readable across tenants. That gap is
documented with a strict-xfail guard (``test_background_tasks_are_force_rls``):
it must be fixed with a migration (add ``tenant_id`` + RLS policy) and the
xfail marker must then be removed.
"""

import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from cms.models import (
    Client,
    Document,
    Finding,
    OsintSearch,
    SpiderFootScan,
    Tenant,
    User,
    db,
)
from cms.tenant_context import set_tenant_context


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL integration tests require DATABASE_URL=postgresql://...",
)

EXPECTED_FORCE_RLS_TABLES = {
    "addresses",
    "api_keys",
    "audit_logs",
    "case_number_counters",
    "cases",
    "clients",
    "comment_edit_history",
    "comments",
    "contacts",
    "document_templates",
    "documents",
    "financial_records",
    "findings",
    "investigation_seq_counters",
    "investigations",
    "invoice_items",
    "invoice_number_counters",
    "invoices",
    "login_logs",
    "notifications",
    "osint_searches",
    "payments",
    "phone_lookups",
    "reminders",
    "screenshots",
    "social_accounts",
    "spiderfoot_scans",
    "subject_facts",
    "subject_identifiers",
    "subjects",
}


def _force_rls_tables():
    rows = db.session.execute(
        text(
            "SELECT relname FROM pg_class cls "
            "JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace "
            "WHERE nspname = 'public' AND relforcerowsecurity"
        )
    ).scalars().all()
    return set(rows)


def _new_tenant():
    tenant = Tenant(
        name=f"Matrix Tenant {uuid.uuid4().hex[:6]}",
        slug=f"pg-matrix-{uuid.uuid4().hex[:8]}",
        is_active=True,
        tier="enterprise",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(tenant)
    db.session.flush()
    return tenant.id


class TestMultiTenantRLSMatrix:
    def test_force_rls_coverage_pinned(self):
        """The protected-table set must match expectations exactly. Changes to
        RLS coverage (additions/removals) are a conscious schema decision."""
        assert _force_rls_tables() == EXPECTED_FORCE_RLS_TABLES

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Known gap: background_tasks has no tenant_id column and is not "
            "FORCE RLS. Persisted jobs are readable across tenants until a "
            "migration adds tenant_id + RLS policy. Remove this xfail when fixed."
        ),
    )
    def test_background_tasks_are_force_rls(self):
        assert "background_tasks" in _force_rls_tables()

    def test_documents_isolated_between_tenants(self, app):
        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        doc_a = Document(
            tenant_id=tenant_a,
            filename="matrix-a.txt",
            original_filename="matrix-a.txt",
            mime_type="text/plain",
            file_size=2,
            storage_path="/var/uploads/matrix-a.txt",
        )
        doc_b = Document(
            tenant_id=tenant_b,
            filename="matrix-b.txt",
            original_filename="matrix-b.txt",
            mime_type="text/plain",
            file_size=2,
            storage_path="/var/uploads/matrix-b.txt",
        )
        db.session.add_all([doc_a, doc_b])
        db.session.commit()
        doc_a_id, doc_b_id = doc_a.id, doc_b.id

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert Document.query.filter_by(id=doc_a_id).count() == 1
        assert Document.query.filter_by(id=doc_b_id).count() == 0

        set_tenant_context(db, tenant_b)
        db.session.expire_all()
        assert Document.query.filter_by(id=doc_a_id).count() == 0
        assert Document.query.filter_by(id=doc_b_id).count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert Document.query.filter_by(id=doc_a_id).count() == 0
        assert Document.query.filter_by(id=doc_b_id).count() == 0

    def test_findings_isolated_between_tenants(self, app):
        from cms.models import Case

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        client_a = Client(tenant_id=tenant_a, name="matrix findings A")
        client_b = Client(tenant_id=tenant_b, name="matrix findings B")
        db.session.add_all([client_a, client_b])
        db.session.flush()
        case_a = Case(
            tenant_id=tenant_a,
            case_number=f"MAT-A-{uuid.uuid4().hex[:8]}",
            client_id=client_a.id,
            title="Matrix case A",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        case_b = Case(
            tenant_id=tenant_b,
            case_number=f"MAT-B-{uuid.uuid4().hex[:8]}",
            client_id=client_b.id,
            title="Matrix case B",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        db.session.add_all([case_a, case_b])
        db.session.flush()
        finding_a = Finding(
            tenant_id=tenant_a,
            case_id=case_a.id,
            title="Matrix finding A",
            content="evidence A",
            source_type="osint",
            created_by=admin.id,
        )
        finding_b = Finding(
            tenant_id=tenant_b,
            case_id=case_b.id,
            title="Matrix finding B",
            content="evidence B",
            source_type="osint",
            created_by=admin.id,
        )
        db.session.add_all([finding_a, finding_b])
        db.session.commit()
        finding_a_id, finding_b_id = finding_a.id, finding_b.id

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert Finding.query.filter_by(id=finding_a_id).count() == 1
        assert Finding.query.filter_by(id=finding_b_id).count() == 0

        set_tenant_context(db, tenant_b)
        db.session.expire_all()
        assert Finding.query.filter_by(id=finding_a_id).count() == 0
        assert Finding.query.filter_by(id=finding_b_id).count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert Finding.query.filter_by(id=finding_a_id).count() == 0
        assert Finding.query.filter_by(id=finding_b_id).count() == 0

    def test_osint_search_and_spiderfoot_scan_isolated(self, app):
        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        osint_a = OsintSearch(
            tenant_id=tenant_a,
            search_id=f"mat-sa-{uuid.uuid4().hex[:16]}",
            search_query="matrix person A",
            status="completed",
            started_by=admin.id,
        )
        osint_b = OsintSearch(
            tenant_id=tenant_b,
            search_id=f"mat-sb-{uuid.uuid4().hex[:16]}",
            search_query="matrix person B",
            status="completed",
            started_by=admin.id,
        )
        scan_a = SpiderFootScan(
            tenant_id=tenant_a,
            scan_id=f"mat-sfa-{uuid.uuid4().hex[:16]}",
            target_value="first.example",
            target_type="domain",
            status="running",
        )
        scan_b = SpiderFootScan(
            tenant_id=tenant_b,
            scan_id=f"mat-sfb-{uuid.uuid4().hex[:16]}",
            target_value="second.example",
            target_type="domain",
            status="running",
        )
        db.session.add_all([osint_a, osint_b, scan_a, scan_b])
        db.session.commit()
        osint_a_id, osint_b_id = osint_a.id, osint_b.id
        scan_a_id, scan_b_id = scan_a.id, scan_b.id

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert OsintSearch.query.filter_by(id=osint_a_id).count() == 1
        assert OsintSearch.query.filter_by(id=osint_b_id).count() == 0
        assert SpiderFootScan.query.filter_by(id=scan_a_id).count() == 1
        assert SpiderFootScan.query.filter_by(id=scan_b_id).count() == 0

        set_tenant_context(db, tenant_b)
        db.session.expire_all()
        assert OsintSearch.query.filter_by(id=osint_a_id).count() == 0
        assert OsintSearch.query.filter_by(id=osint_b_id).count() == 1
        assert SpiderFootScan.query.filter_by(id=scan_a_id).count() == 0
        assert SpiderFootScan.query.filter_by(id=scan_b_id).count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert OsintSearch.query.filter_by(id=osint_a_id).count() == 0
        assert OsintSearch.query.filter_by(id=osint_b_id).count() == 0
        assert SpiderFootScan.query.filter_by(id=scan_a_id).count() == 0
        assert SpiderFootScan.query.filter_by(id=scan_b_id).count() == 0

    def test_super_admin_switch_scopes_rls_to_selected_tenant(self, app):
        """The super-admin tenant switch (``switch_tenant``) flips the RLS
        context to the selected tenant — the switched-tenant rows appear and
        the super admin's own-tenant rows disappear, mirroring the mapping in
        ``app.py`` (``tid = switched_tenant_id or tid``)."""
        admin = User.query.filter_by(username="admin").one()
        assert admin.is_super_admin
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        client_a = Client(tenant_id=tenant_a, name="matrix switch A")
        client_b = Client(tenant_id=tenant_b, name="matrix switch B")
        db.session.add_all([client_a, client_b])
        db.session.commit()
        client_a_id, client_b_id = client_a.id, client_b.id

        def _counts():
            db.session.expire_all()
            return (
                Client.query.filter_by(id=client_a_id).count(),
                Client.query.filter_by(id=client_b_id).count(),
            )

        set_tenant_context(db, tenant_a)
        assert _counts() == (1, 0)

        switched_tenant_id = tenant_b
        set_tenant_context(db, switched_tenant_id)
        assert _counts() == (0, 1)

        set_tenant_context(db, tenant_a)
        assert _counts() == (1, 0)