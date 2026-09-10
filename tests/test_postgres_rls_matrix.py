"""Two-tenant RLS isolation matrix on real PostgreSQL.

Extends ``test_postgres_integration`` with per-domain isolation coverage for
the OSINT working domains that suite did not exercise directly: documents
(uploads / report exports), findings (report evidence), osint_searches +
spiderfoot_scans (background jobs), and the super-admin tenant switch flow
(``POST /cms/switch-tenant/<id>``). It also pins the actual FORCE-RLS table
set so schema coverage stays a conscious decision.

Known gap: none. ``background_tasks`` was added to FORCE RLS via
``e2f3a4b5c6d7`` (tenant_id column + policy) — the previous strict-xfail
guard was removed and replaced by a positive isolation test.
"""

import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cms.models import (
    ActionFinding,
    BackgroundTask,
    Client,
    Document,
    Finding,
    OsintSearch,
    ResearchAction,
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
    "action_findings",
    "addresses",
    "api_keys",
    "audit_logs",
    "background_tasks",
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
    "research_actions",
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

    def test_background_tasks_are_force_rls(self):
        """``background_tasks`` is now FORCE RLS (migration e2f3a4b5c6d7)."""
        assert "background_tasks" in _force_rls_tables()

    def test_background_tasks_isolated_between_tenants(self, app):
        """Persisted background jobs must be scoped to their owning tenant.

        Covered now that ``background_tasks`` is FORCE RLS: a task enqueued by
        tenant A is invisible as tenant B, and the web status endpoint only
        exposes the current tenant's tasks (via the request RLS context).
        """
        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        task_a = BackgroundTask(
            id=f"mat-bt-a-{uuid.uuid4().hex[:16]}",
            status="completed",
            task_name="async_email",
            tenant_id=tenant_a,
        )
        task_b = BackgroundTask(
            id=f"mat-bt-b-{uuid.uuid4().hex[:16]}",
            status="completed",
            task_name="async_email",
            tenant_id=tenant_b,
        )
        db.session.add_all([task_a, task_b])
        db.session.commit()
        task_a_id, task_b_id = task_a.id, task_b.id

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert BackgroundTask.query.filter_by(id=task_a_id).count() == 1
        assert BackgroundTask.query.filter_by(id=task_b_id).count() == 0

        set_tenant_context(db, tenant_b)
        db.session.expire_all()
        assert BackgroundTask.query.filter_by(id=task_a_id).count() == 0
        assert BackgroundTask.query.filter_by(id=task_b_id).count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert BackgroundTask.query.filter_by(id=task_a_id).count() == 0
        assert BackgroundTask.query.filter_by(id=task_b_id).count() == 0

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

    def test_research_actions_are_force_rls(self):
        """``research_actions`` is added to FORCE RLS in f6a7b8c9d0e1."""
        assert "research_actions" in _force_rls_tables()

    def test_action_findings_are_force_rls(self):
        """``action_findings`` (junction) is FORCE RLS in f6a7b8c9d0e1."""
        assert "action_findings" in _force_rls_tables()

    def test_research_actions_isolated_between_tenants(self, app):
        """Job data (research_actions) must be invisible across tenants and
        invisible without any tenant context (the cold-worker baseline)."""
        from cms.models import Case

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        client_a = Client(tenant_id=tenant_a, name="matrix ra A")
        client_b = Client(tenant_id=tenant_b, name="matrix ra B")
        db.session.add_all([client_a, client_b])
        db.session.flush()
        case_a = Case(
            tenant_id=tenant_a,
            case_number=f"MAT-RA-A-{uuid.uuid4().hex[:8]}",
            client_id=client_a.id,
            title="Matrix RA A",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        case_b = Case(
            tenant_id=tenant_b,
            case_number=f"MAT-RA-B-{uuid.uuid4().hex[:8]}",
            client_id=client_b.id,
            title="Matrix RA B",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        db.session.add_all([case_a, case_b])
        db.session.flush()
        action_a = ResearchAction(
            tenant_id=tenant_a,
            case_id=case_a.id,
            action_type="google_dork",
            data_value='{"dork": "site:x"}',
            status="completed",
            created_by=admin.id,
        )
        action_b = ResearchAction(
            tenant_id=tenant_b,
            case_id=case_b.id,
            action_type="google_dork",
            data_value='{"dork": "site:y"}',
            status="completed",
            created_by=admin.id,
        )
        db.session.add_all([action_a, action_b])
        db.session.commit()
        action_a_id, action_b_id = action_a.id, action_b.id

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert ResearchAction.query.filter_by(id=action_a_id).count() == 1
        assert ResearchAction.query.filter_by(id=action_b_id).count() == 0

        set_tenant_context(db, tenant_b)
        db.session.expire_all()
        assert ResearchAction.query.filter_by(id=action_a_id).count() == 0
        assert ResearchAction.query.filter_by(id=action_b_id).count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert ResearchAction.query.filter_by(id=action_a_id).count() == 0
        assert ResearchAction.query.filter_by(id=action_b_id).count() == 0

    def test_action_findings_isolated_between_tenants(self, app):
        """Junction rows inherit the tenant of their research action: a link
        between a tenant-A action and a tenant-A finding is readable by tenant
        A and by nobody else."""
        from cms.models import Case

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        client_a = Client(tenant_id=tenant_a, name="matrix jx A")
        client_b = Client(tenant_id=tenant_b, name="matrix jx B")
        db.session.add_all([client_a, client_b])
        db.session.flush()
        case_a = Case(
            tenant_id=tenant_a,
            case_number=f"MAT-JX-A-{uuid.uuid4().hex[:8]}",
            client_id=client_a.id,
            title="Matrix JX A",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        case_b = Case(
            tenant_id=tenant_b,
            case_number=f"MAT-JX-B-{uuid.uuid4().hex[:8]}",
            client_id=client_b.id,
            title="Matrix JX B",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        db.session.add_all([case_a, case_b])
        db.session.flush()
        action_a = ResearchAction(
            tenant_id=tenant_a,
            case_id=case_a.id,
            action_type="manual_entry",
            status="completed",
            created_by=admin.id,
        )
        action_b = ResearchAction(
            tenant_id=tenant_b,
            case_id=case_b.id,
            action_type="manual_entry",
            status="completed",
            created_by=admin.id,
        )
        finding_a = Finding(
            tenant_id=tenant_a,
            case_id=case_a.id,
            title="Matrix jx finding A",
            content="evidence",
            source_type="manual",
            created_by=admin.id,
        )
        finding_b = Finding(
            tenant_id=tenant_b,
            case_id=case_b.id,
            title="Matrix jx finding B",
            content="evidence",
            source_type="manual",
            created_by=admin.id,
        )
        db.session.add_all([action_a, action_b, finding_a, finding_b])
        db.session.flush()
        link_a = ActionFinding(action_id=action_a.id, finding_id=finding_a.id)
        link_b = ActionFinding(action_id=action_b.id, finding_id=finding_b.id)
        db.session.add_all([link_a, link_b])
        db.session.commit()
        act_a_id, act_b_id = action_a.id, action_b.id

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert ActionFinding.query.filter_by(action_id=act_a_id).count() == 1
        assert ActionFinding.query.filter_by(action_id=act_b_id).count() == 0

        set_tenant_context(db, tenant_b)
        db.session.expire_all()
        assert ActionFinding.query.filter_by(action_id=act_a_id).count() == 0
        assert ActionFinding.query.filter_by(action_id=act_b_id).count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert ActionFinding.query.filter_by(action_id=act_a_id).count() == 0
        assert ActionFinding.query.filter_by(action_id=act_b_id).count() == 0

    def test_force_rls_rejects_cross_tenant_action_insert(self, app):
        """WITH CHECK must reject inserting a research way-action whose
        tenant_id differs from the active context (no bypass)."""
        from cms.models import Case

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        client = Client(tenant_id=tenant_a, name="matrix wc")
        db.session.add(client)
        db.session.flush()
        case = Case(
            tenant_id=tenant_a,
            case_number=f"MAT-WC-{uuid.uuid4().hex[:8]}",
            client_id=client.id,
            title="Matrix WC",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        db.session.add(case)
        db.session.commit()
        case_id = case.id

        set_tenant_context(db, tenant_a)
        db.session.add(
            ResearchAction(
                tenant_id=tenant_b,
                case_id=case_id,
                action_type="manual_entry",
                status="pending",
                created_by=admin.id,
            )
        )
        with pytest.raises(DBAPIError) as exc_info:
            db.session.commit()
        assert getattr(exc_info.value.orig, "pgcode", None) == "42501"
        db.session.rollback()

    def test_action_findings_with_check_derives_tenant_from_parent(self, app):
        """A junction row for another tenant's action must be rejected under a
        normal tenant context (subquery policy evaluates against the parent)."""
        from cms.models import Case

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = _new_tenant()

        set_tenant_context(db, None, bypass_rls=True)
        client_b = Client(tenant_id=tenant_b, name="matrix jb")
        db.session.add(client_b)
        db.session.flush()
        case_b = Case(
            tenant_id=tenant_b,
            case_number=f"MAT-JB-{uuid.uuid4().hex[:8]}",
            client_id=client_b.id,
            title="Matrix JB",
            start_date=datetime.utcnow().date(),
            created_by=admin.id,
        )
        db.session.add(case_b)
        db.session.flush()
        action_b = ResearchAction(
            tenant_id=tenant_b,
            case_id=case_b.id,
            action_type="manual_entry",
            status="completed",
            created_by=admin.id,
        )
        finding_b = Finding(
            tenant_id=tenant_b,
            case_id=case_b.id,
            title="Matrix jb finding",
            content="evidence",
            source_type="manual",
            created_by=admin.id,
        )
        db.session.add_all([action_b, finding_b])
        db.session.commit()
        act_b_id, finding_b_id = action_b.id, finding_b.id

        set_tenant_context(db, tenant_a)
        db.session.add(
            ActionFinding(action_id=act_b_id, finding_id=finding_b_id)
        )
        with pytest.raises(DBAPIError) as exc_info:
            db.session.commit()
        assert getattr(exc_info.value.orig, "pgcode", None) == "42501"
        db.session.rollback()

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