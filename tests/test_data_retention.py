"""Regression tests for the tenant data-retention purge."""

from cms.data_retention import _PURGE_ORDER, _purge_single_tenant
import uuid

from cms.models import Tenant, User, db


REQUIRED_TENANT_TABLES = {
    "finding_capture_jobs",
    "background_tasks",
    "finding_screenshots",
    "subject_identifiers",
    "subject_facts",
    "research_actions",
    "investigations",
    "case_number_counters",
    "investigation_seq_counters",
    "invoice_number_counters",
    "service_rates",
    "proration_logs",
    "audit_logs",
}


def test_purge_order_covers_known_tenant_tables():
    assert REQUIRED_TENANT_TABLES.issubset(_PURGE_ORDER)
    assert _PURGE_ORDER.index("finding_capture_jobs") < _PURGE_ORDER.index("findings")
    assert _PURGE_ORDER.index("research_actions") < _PURGE_ORDER.index("cases")
    assert _PURGE_ORDER.index("audit_logs") < _PURGE_ORDER.index("users")


def test_tenant_purge_dry_run_executes_and_rolls_back(app, db_session):
    with app.app_context():
        tenant = User.query.filter_by(username="admin").first().tenant
        _purge_single_tenant(tenant, dry_run=True)

        assert User.query.filter_by(tenant_id=tenant.id).count() >= 1


def test_tenant_purge_keeps_other_tenant_data(app, db_session):
    with app.app_context():
        suffix = uuid.uuid4().hex[:12]
        tenant_a = Tenant(
            name=f"Purge A {suffix}",
            slug=f"purge-a-{suffix}",
            join_code=uuid.uuid4().hex[:20],
        )
        tenant_b = Tenant(
            name=f"Purge B {suffix}",
            slug=f"purge-b-{suffix}",
            join_code=uuid.uuid4().hex[:20],
        )
        db.session.add_all([tenant_a, tenant_b])
        db.session.flush()

        user_a = User(
            username=f"purge-a-{suffix}",
            email=f"purge-a-{suffix}@localhost",
            full_name="Purge A User",
            role="investigator",
            tenant_id=tenant_a.id,
            is_active=True,
        )
        user_b = User(
            username=f"purge-b-{suffix}",
            email=f"purge-b-{suffix}@localhost",
            full_name="Purge B User",
            role="investigator",
            tenant_id=tenant_b.id,
            is_active=True,
        )
        user_a.set_password("Test1234!")
        user_b.set_password("Test1234!")
        db.session.add_all([user_a, user_b])
        db.session.commit()
        tenant_a_id = tenant_a.id
        tenant_b_id = tenant_b.id
        user_a_id = user_a.id
        user_b_id = user_b.id

        _purge_single_tenant(tenant_a, dry_run=False)
        db.session.commit()

        assert db.session.get(Tenant, tenant_a_id) is None
        assert db.session.get(Tenant, tenant_b_id) is not None
        assert db.session.get(User, user_a_id) is None
        assert db.session.get(User, user_b_id) is not None


def test_background_runner_restores_task_tenant_context(app, db_session):
    from flask import g

    from cms.background import _run_task
    from cms.models import BackgroundTask

    with app.app_context():
        tenant = User.query.filter_by(username="admin").first().tenant
        tenant_id = tenant.id
        task_id = f"tenant-context-{uuid.uuid4().hex}"
        db.session.add(
            BackgroundTask(
                id=task_id,
                task_name="context_probe",
                status="pending",
                tenant_id=tenant_id,
            )
        )
        db.session.commit()
        observed = []

        def context_probe():
            observed.append(g.get("tenant_id"))
            return True

        _run_task(task_id, context_probe)

        assert observed == [tenant_id]
