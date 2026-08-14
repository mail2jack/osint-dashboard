"""Mandatory PostgreSQL integration coverage for tenant isolation."""

import os
from datetime import date
import uuid

import pytest
from sqlalchemy import text

from cms.models import Case, Client, Notification, Tenant, User, db
from cms.tenant_context import set_tenant_context


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL integration tests require DATABASE_URL=postgresql://...",
)


def _seed_case(tenant_id: str, created_by: str) -> Case:
    suffix = uuid.uuid4().hex[:8]
    client = Client(tenant_id=tenant_id, name=f"PG client {suffix}")
    db.session.add(client)
    db.session.flush()
    case = Case(
        tenant_id=tenant_id,
        case_number=f"PG-{suffix}",
        client_id=client.id,
        title=f"PG case {suffix}",
        start_date=date.today(),
        created_by=created_by,
    )
    db.session.add(case)
    db.session.flush()
    return case


class TestPostgreSQLIntegration:
    def test_migrations_reach_head_and_enable_forced_rls(self, app):
        revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
        assert revision == "e0f1a2b3c4d5"

        protected = db.session.execute(
            text(
                """
                SELECT count(*)
                FROM pg_class
                WHERE relname IN ('cases', 'clients', 'notifications')
                  AND relrowsecurity
                  AND relforcerowsecurity
                """
            )
        ).scalar()
        assert protected == 3

    def test_rls_hides_other_tenant_cases(self, app):
        admin = User.query.filter_by(username="admin").one()
        tenant_b = Tenant(
            name="Postgres Tenant B",
            slug=f"pg-b-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()

        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case_a = _seed_case(admin.tenant_id, admin.id)
        case_b = _seed_case(tenant_b.id, admin.id)
        db.session.commit()

        set_tenant_context(db, admin.tenant_id)
        assert Case.query.filter_by(id=case_a.id).count() == 1
        assert Case.query.filter_by(id=case_b.id).count() == 0

        set_tenant_context(db, tenant_b.id)
        assert Case.query.filter_by(id=case_a.id).count() == 0
        assert Case.query.filter_by(id=case_b.id).count() == 1

    def test_case_access_query_is_tenant_scoped(self, app):
        admin = User.query.filter_by(username="admin").one()
        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case = _seed_case(admin.tenant_id, admin.id)
        db.session.commit()

        set_tenant_context(db, admin.tenant_id)
        assert Case.query.get(case.id) is not None

        set_tenant_context(db, "tenant-that-does-not-exist")
        assert Case.query.get(case.id) is None

    def test_worker_sets_tenant_context(self, app, monkeypatch):
        admin = User.query.filter_by(username="admin").one()
        import cms.tasks as tasks

        monkeypatch.setattr(tasks, "_get_app", lambda: app)
        set_tenant_context(db, None, bypass_rls=True)
        assert tasks.send_notification_task(
            str(admin.id), "system", "Worker test", "Created in worker context"
        )

        set_tenant_context(db, admin.tenant_id)
        notification = Notification.query.filter_by(
            message="Created in worker context"
        ).one()
        assert notification.tenant_id == admin.tenant_id

    def test_cli_context_is_explicit(self):
        source = open("scripts/notify_update.py", encoding="utf-8").read()
        assert "set_tenant_context(db, None, bypass_rls=True)" in source
