"""Mandatory PostgreSQL integration coverage for tenant isolation."""

import os
from datetime import UTC, date, datetime
import uuid

import pytest
from sqlalchemy import text

from cms.models import (
    AuditLog,
    Case,
    CaseNumberCounter,
    Client,
    Investigation,
    InvestigationSeqCounter,
    LoginLog,
    Notification,
    Tenant,
    User,
    db,
)
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
        # Keep this assertion aligned with the current Alembic head.
        # dd1e2f3a4b5c7 (ADR-0002 PR3 P1) adds investigation identity
        # triggers (case_id/tenant_id immutable) on top of bb1c2d3e4f5a7.
        assert revision == "dd1e2f3a4b5c7"

        protected = db.session.execute(
            text(
                """
                SELECT count(*)
                FROM pg_class
                WHERE relname IN (
                    'cases', 'clients', 'notifications',
                    'subject_identifiers', 'subject_facts',
                    'investigations', 'case_number_counters',
                    'investigation_seq_counters'
                )
                  AND relrowsecurity
                  AND relforcerowsecurity
                """
            )
        ).scalar()
        assert protected == 8

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
        case_a_id = case_a.id
        case_b_id = case_b.id
        db.session.commit()

        set_tenant_context(db, admin.tenant_id)
        settings = db.session.execute(
            text(
                "SELECT current_setting('app.tenant_id'), current_setting('app.bypass_rls')"
            )
        ).one()
        assert settings == (admin.tenant_id, "false")
        db.session.expire_all()
        assert Case.query.filter_by(id=case_a_id).count() == 1
        assert Case.query.filter_by(id=case_b_id).count() == 0

        set_tenant_context(db, tenant_b.id)
        db.session.expire_all()
        assert Case.query.filter_by(id=case_a_id).count() == 0
        assert Case.query.filter_by(id=case_b_id).count() == 1

    def test_case_access_query_is_tenant_scoped(self, app):
        admin = User.query.filter_by(username="admin").one()
        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case = _seed_case(admin.tenant_id, admin.id)
        case_id = case.id
        db.session.commit()

        set_tenant_context(db, admin.tenant_id)
        db.session.expire_all()
        assert Case.query.filter_by(id=case_id).count() == 1

        set_tenant_context(db, "tenant-that-does-not-exist")
        db.session.expire_all()
        assert Case.query.filter_by(id=case_id).count() == 0

    def test_case_assignment_access_is_tenant_scoped(self, app):
        admin = User.query.filter_by(username="admin").one()
        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        investigator = User(
            username=f"pg-inv-{uuid.uuid4().hex[:8]}",
            email=f"pg-inv-{uuid.uuid4().hex[:8]}@localhost",
            full_name="Postgres Investigator",
            role="investigator",
            is_active=True,
            tenant_id=admin.tenant_id,
        )
        investigator.set_password("Test1234!")
        db.session.add(investigator)
        db.session.flush()
        case = _seed_case(admin.tenant_id, admin.id)
        db.session.commit()

        assert not investigator.can_access_case(case)
        case.investigators.append(investigator)
        db.session.commit()
        assert investigator.can_access_case(case)

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

    def test_run_action_scopes_rows_under_rls_and_keeps_encryption(self, app):
        """run_action() sets an explicit RLS context; findings/screenshots it
        creates must be visible in the action's tenant and invisible to other
        tenants under real PostgreSQL RLS, and subject identifiers must stay
        ciphertext at rest."""
        import uuid

        from cms.encryption_utils import encryptor
        from cms.models import (
            Finding,
            ResearchAction,
            Subject,
            Case,
            db as _db,
        )
        from cms.workflow.actions.registry import (
            ACTION_REGISTRY,
            register_action,
            run_action,
        )

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = Tenant(
            name="Postgres Enc Tenant B",
            slug=f"pg-enc-b-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()

        set_tenant_context(db, tenant_a, bypass_rls=True)
        client = Client(tenant_id=tenant_a, name="PG enc client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            tenant_id=tenant_a,
            case_number=f"PG-ENC-{uuid.uuid4().hex[:8]}",
            client_id=client.id,
            title="PG enc case",
            start_date=date.today(),
            created_by=admin.id,
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(
            name="PG enc person",
            subject_type="person",
            tenant_id=tenant_a,
            email="pg-enc@example.com",
        )
        subject.encrypt_identifiers()
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        action = ResearchAction(
            case_id=case.id,
            tenant_id=tenant_a,
            action_type="test_pg_enc_probe",
            status="pending",
            created_by=admin.id,
        )
        db.session.add(action)
        db.session.commit()

        def _handler(act):
            c = _db.session.get(Case, act.case_id)
            for s in c.subjects:
                s.decrypt_identifiers()
            target = c.subjects[0] if c.subjects else None
            return [
                {
                    "title": "PG probe finding",
                    "detail": "probe",
                    "subject_id": target.id if target else None,
                    "screenshots": [{"url": None, "source_url": "https://pg.example"}],
                }
            ]

        register_action(
            "test_pg_enc_probe",
            "PG Probe",
            "probe",
            _handler,
            category="open",
        )
        try:
            run_action(action.id)
            db.session.expire_all()

            # run_action() resets the connection context afterwards (no leak
            # into the pool); re-scope to the action's tenant to verify.
            set_tenant_context(db, tenant_a)
            db.session.expire_all()

            # Ciphertext at rest, verifiable via raw SQL (no ORM decrypts).
            stored = db.session.execute(
                text("SELECT email FROM subjects WHERE id = :id"),
                {"id": subject.id},
            ).scalar_one()
            assert stored != "pg-enc@example.com"
            assert encryptor.decrypt(stored) == "pg-enc@example.com"

            finding = (
                Finding.query.filter_by(case_id=case.id)
                .order_by(Finding.created_at.desc())
                .first()
            )
            assert finding is not None
            assert finding.tenant_id == tenant_a
            finding_id = finding.id
            db.session.expire_all()

            # RLS must hide the produced evidence from another tenant.
            set_tenant_context(db, tenant_b.id)
            db.session.expire_all()
            assert Finding.query.filter_by(id=finding_id).count() == 0

            set_tenant_context(db, tenant_a)
            db.session.expire_all()
            assert Finding.query.filter_by(id=finding_id).count() == 1
        finally:
            ACTION_REGISTRY.pop("test_pg_enc_probe", None)
            set_tenant_context(db, admin.tenant_id)

    def test_run_action_cold_worker_resets_tenant_context(self, app):
        """A worker that starts with NO tenant context (no request/flask.g)
        must still run: run_action() first looks the action up under a
        temporary RLS bypass, then scopes the run to the action's tenant, and
        finally resets the connection context so nothing leaks to the pool."""
        import uuid

        from cms.models import Finding, ResearchAction, Subject, Case
        from cms.workflow.actions.registry import (
            ACTION_REGISTRY,
            register_action,
            run_action,
        )

        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id

        set_tenant_context(db, tenant_a, bypass_rls=True)
        client = Client(tenant_id=tenant_a, name="PG cold client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            tenant_id=tenant_a,
            case_number=f"PG-COLD-{uuid.uuid4().hex[:8]}",
            client_id=client.id,
            title="PG cold case",
            start_date=date.today(),
            created_by=admin.id,
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(
            name="PG cold person",
            subject_type="person",
            tenant_id=tenant_a,
            email="pg-cold@example.com",
        )
        subject.encrypt_identifiers()
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        action = ResearchAction(
            case_id=case.id,
            tenant_id=tenant_a,
            action_type="test_pg_cold_probe",
            status="pending",
            created_by=admin.id,
        )
        db.session.add(action)
        db.session.commit()

        # Simulate a cold worker: start with NO tenant context at all.
        set_tenant_context(db, None)
        db.session.expire_all()
        assert (
            db.session.execute(text("SELECT current_setting('app.tenant_id')")).scalar()
            == ""
        )

        def _handler(act):
            c = db.session.get(Case, act.case_id)
            for s in c.subjects:
                s.decrypt_identifiers()
            return [
                {
                    "title": "PG cold finding",
                    "detail": "cold",
                    "subject_id": c.subjects[0].id if c.subjects else None,
                }
            ]

        register_action(
            "test_pg_cold_probe",
            "PG Cold Probe",
            "cold",
            _handler,
            category="open",
        )
        try:
            run_action(action.id)
            db.session.expire_all()

            # Context must be reset afterwards: no tenant and no bypass leak
            # into the next task on the pooled connection.
            tenant_id, bypass = db.session.execute(
                text(
                    "SELECT current_setting('app.tenant_id'), "
                    "current_setting('app.bypass_rls')"
                )
            ).one()
            assert tenant_id == ""
            assert bypass == "false"

            set_tenant_context(db, tenant_a)
            db.session.expire_all()
            reloaded = db.session.get(ResearchAction, action.id)
            assert reloaded.status == "completed"
            finding = (
                Finding.query.filter_by(case_id=case.id)
                .order_by(Finding.created_at.desc())
                .first()
            )
            assert finding is not None
            assert finding.tenant_id == tenant_a
        finally:
            ACTION_REGISTRY.pop("test_pg_cold_probe", None)
            set_tenant_context(db, admin.tenant_id)

    def test_login_under_force_rls(self, app, client):
        """Login must succeed under FORCE RLS — audit_logs INSERT must carry
        tenant context set by the auth route before writing."""
        with app.app_context():
            force = db.session.execute(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'audit_logs'"
                )
            ).scalar()
            assert force is True, "FORCE RLS must be active for this test"

            admin = User.query.filter_by(role="admin").first()
            admin.totp_secret = None
            admin.totp_enabled = False
            db.session.commit()
            tenant_id = admin.tenant_id

        resp = client.post(
            "/auth/login",
            data={"email": "admin@localhost", "password": "Test1234!"},
            follow_redirects=False,
        )
        assert resp.status_code in (
            302,
            200,
        ), f"login returned {resp.status_code} — FORCE RLS blocked audit_logs INSERT"

        with app.app_context():
            entry = AuditLog.query.filter_by(
                user_id=admin.id, action="password_verified"
            ).first()
            assert entry is not None, "audit_logs entry not created after login"
            assert entry.tenant_id == tenant_id

    def test_async_login_log_under_force_rls(self, app):
        """log_login_attempt(run_async=True) must persist a LoginLog record
        with the correct tenant_id under FORCE RLS.  The background thread
        receives the Flask app object explicitly so it can create its own
        app context — it must NOT rely on current_app from the parent."""
        import time

        with app.app_context():
            admin = User.query.filter_by(role="admin").first()
            tenant_id = admin.tenant_id

            from cms.geo_utils import log_login_attempt

            log_login_attempt(
                user_id=admin.id,
                ip_address="127.0.0.1",
                is_success=False,
                user_agent="test-agent",
                tenant_id=tenant_id,
                run_async=True,
            )

            deadline = time.time() + 5
            while time.time() < deadline:
                if LoginLog.query.filter_by(user_id=admin.id).count() > 0:
                    break
                time.sleep(0.1)

            entry = LoginLog.query.filter_by(user_id=admin.id).first()
            assert entry is not None, (
                "login_logs INSERT did not complete in background thread"
            )
            assert entry.tenant_id == tenant_id
            assert entry.ip_address == "127.0.0.1"
            assert entry.is_success is False

    def test_set_tenant_context_recovers_from_failed_session(self, app):
        """After a SQL error that puts the PostgreSQL transaction in a failed
        state, set_tenant_context() must recover by rolling back first, then
        re-establish the RLS context so writes succeed under FORCE RLS."""
        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id

        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.commit()

        with pytest.raises(Exception):
            db.session.execute(text("SELECT 1 / 0"))

        set_tenant_context(db, tenant_id)

        tenant_id_now, bypass_now = db.session.execute(
            text(
                "SELECT current_setting('app.tenant_id'), "
                "current_setting('app.bypass_rls')"
            )
        ).one()
        assert tenant_id_now == tenant_id
        assert bypass_now == "false"

        client = Client(tenant_id=tenant_id, name="PG recovery client")
        db.session.add(client)
        db.session.commit()

        set_tenant_context(db, None)
        db.session.expire_all()
        assert Client.query.filter_by(name="PG recovery client").count() == 0

        set_tenant_context(db, tenant_id)
        db.session.expire_all()
        assert Client.query.filter_by(name="PG recovery client").count() == 1

    def test_set_tenant_context_does_not_swallow_non_25p02_errors(
        self, app, monkeypatch
    ):
        """Only InFailedSqlTransaction (25P02) may trigger rollback + retry.
        Other DBAPIErrors — e.g. permission denied, connection lost — must
        propagate immediately without rolling back a healthy transaction."""
        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id

        set_tenant_context(db, tenant_id, bypass_rls=True)
        client = Client(tenant_id=tenant_id, name="PG pre-error client")
        db.session.add(client)
        db.session.flush()

        original_execute = db.session.execute
        rollback_called = False
        call_count = 0

        class _FakePGError(Exception):
            pgcode = "42501"

        def _intercepting_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                from sqlalchemy.exc import DBAPIError

                raise DBAPIError("stmt", "params", _FakePGError("denied"))
            return original_execute(*args, **kwargs)

        monkeypatch.setattr(db.session, "execute", _intercepting_execute)

        original_rollback = db.session.rollback

        def _tracking_rollback(*a, **kw):
            nonlocal rollback_called
            rollback_called = True
            return original_rollback(*a, **kw)

        monkeypatch.setattr(db.session, "rollback", _tracking_rollback)

        with pytest.raises(Exception) as exc_info:
            set_tenant_context(db, tenant_id)
        assert getattr(exc_info.value.orig, "pgcode", None) == "42501"
        assert not rollback_called

        monkeypatch.undo()
        db.session.rollback()

    def test_tenant_context_switching_overrides_previous_values(self, app):
        """set_tenant_context() must fully replace the previous GUC values:
        calling it with a different tenant makes the old tenant's rows
        invisible under RLS, and calling with bypass_rls=False disables
        the bypass.  Pool-level isolation (different connection, clean
        slate) is an entrypoint concern — every web before_request, worker
        task, and CLI script must call set_tenant_context() explicitly."""
        admin = User.query.filter_by(username="admin").one()
        tenant_a = admin.tenant_id
        tenant_b = Tenant(
            name="PG Switch Tenant B",
            slug=f"pg-switch-b-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.commit()

        set_tenant_context(db, tenant_a, bypass_rls=True)
        client = Client(tenant_id=tenant_a, name="PG switch client A")
        db.session.add(client)
        db.session.commit()

        set_tenant_context(db, tenant_b.id, bypass_rls=True)
        client = Client(tenant_id=tenant_b.id, name="PG switch client B")
        db.session.add(client)
        db.session.commit()

        set_tenant_context(db, tenant_a)
        db.session.expire_all()
        assert Client.query.filter_by(name="PG switch client A").count() == 1
        assert Client.query.filter_by(name="PG switch client B").count() == 0

        set_tenant_context(db, tenant_b.id)
        db.session.expire_all()
        assert Client.query.filter_by(name="PG switch client A").count() == 0
        assert Client.query.filter_by(name="PG switch client B").count() == 1

        set_tenant_context(db, None)
        db.session.expire_all()
        assert Client.query.filter_by(name="PG switch client A").count() == 0
        assert Client.query.filter_by(name="PG switch client B").count() == 0


class TestInvestigationRLSAndNumbering:
    """ADR-0002 PR2 on real PostgreSQL: FORCE RLS on the new tables, atomic
    number streams, and the DB-level tenant invariant (belt-and-suspenders
    with the service-layer checks covered in the SQLite suite)."""

    def _seed_case(self, admin, tenant_id: str) -> Case:
        return _seed_case(tenant_id, admin.id)

    def test_allocators_work_under_rls_and_isolate_tenants(self, app):
        admin = User.query.filter_by(username="admin").one()
        tenant_b = Tenant(
            name="PG Inv Tenant B",
            slug=f"pg-inv-b-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        tenant_b_id = tenant_b.id

        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case_a = self._seed_case(admin, admin.tenant_id)
        case_b = self._seed_case(admin, tenant_b_id)
        db.session.commit()

        from cms.services.sequence_service import (
            allocate_case_number,
            allocate_investigation_sequence_no,
            create_investigation,
        )

        # Tenant A context: allocate its case number + first investigations.
        set_tenant_context(db, admin.tenant_id)
        assert allocate_case_number(admin.tenant_id).endswith("-00001")
        assert allocate_investigation_sequence_no(admin.tenant_id, case_a.id) == 1
        create_investigation(
            tenant_id=admin.tenant_id, case_id=case_a.id, title="PG eerste"
        )
        seq_a2 = allocate_investigation_sequence_no(admin.tenant_id, case_a.id)
        db.session.commit()

        # Tenant B context: its counters are fresh and its rows are visible,
        # while tenant A's counters/investigations are invisible under RLS.
        set_tenant_context(db, tenant_b_id)
        assert allocate_investigation_sequence_no(tenant_b_id, case_b.id) == 1
        create_investigation(
            tenant_id=tenant_b_id, case_id=case_b.id, title="PG tweede"
        )
        seq_b2 = allocate_investigation_sequence_no(tenant_b_id, case_b.id)
        assert allocate_case_number(tenant_b_id).endswith("-00001")
        db.session.commit()
        # Re-establish the RLS context: commit() returns the connection to the
        # pool, and the next checkout may carry a different tenant's GUC.
        set_tenant_context(db, tenant_b_id)
        db.session.expire_all()
        assert CaseNumberCounter.query.filter_by(tenant_id=admin.tenant_id).count() == 0
        assert Investigation.query.filter_by(tenant_id=admin.tenant_id).count() == 0
        assert Investigation.query.filter_by(case_id=case_b.id).count() == 1
        assert seq_a2 == 3
        assert seq_b2 == 3

        # Back in tenant A: only its own rows/counters are visible.
        set_tenant_context(db, admin.tenant_id)
        db.session.expire_all()
        assert Investigation.query.filter_by(case_id=case_a.id).count() == 1
        assert CaseNumberCounter.query.filter_by(tenant_id=admin.tenant_id).count() == 1
        assert InvestigationSeqCounter.query.filter_by(
            tenant_id=admin.tenant_id
        ).count() == 1

    def test_composite_fk_rejects_tenant_mismatch_even_with_bypass(self, app):
        """The hard tenant invariant must hold at the DB level too: writing
        an investigation whose (tenant_id, case_id) does not match the case's
        tenant fails the composite foreign key even under RLS bypass."""
        from sqlalchemy.exc import IntegrityError

        admin = User.query.filter_by(username="admin").one()
        tenant_b = Tenant(
            name="PG Inv FK Tenant B",
            slug=f"pg-inv-fk-b-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        tenant_b_id = tenant_b.id

        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case_a = self._seed_case(admin, admin.tenant_id)
        db.session.commit()

        set_tenant_context(db, tenant_b_id, bypass_rls=True)
        bad = Investigation(
            tenant_id=tenant_b_id,
            case_id=case_a.id,
            sequence_no=1,
            title="PG verkeerde tenant",
        )
        db.session.add(bad)
        with pytest.raises(IntegrityError, match="fk_investigations_case_tenant"):
            db.session.flush()
        db.session.rollback()

        # Same for the sequence counter table.
        bad_counter = InvestigationSeqCounter(
            tenant_id=tenant_b_id, case_id=case_a.id, next_seq=1
        )
        db.session.add(bad_counter)
        with pytest.raises(
            IntegrityError, match="fk_investigation_seq_counter_case_tenant"
        ):
            db.session.flush()
        db.session.rollback()

    def test_parallel_allocation_on_postgres(self, app):
        """Concurrent allocations against real PostgreSQL must hand out
        unique and strictly sequential numbers for both streams."""
        import threading

        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        from cms.services.sequence_service import (
            allocate_case_number,
            allocate_investigation_sequence_no,
        )

        admin = User.query.filter_by(username="admin").one()
        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case = self._seed_case(admin, admin.tenant_id)
        case_id = case.id
        tenant_id = admin.tenant_id
        db.session.commit()

        worker_count = 6
        barrier = threading.Barrier(worker_count)
        errors: list[BaseException] = []
        case_nums: list[list[str]] = [[] for _ in range(worker_count)]
        seqs: list[list[int]] = [[] for _ in range(worker_count)]
        # Workers use their own engine/pool: raw sessions inert on ``db.engine``
        # would grow the main pool with several physical connections whose RLS
        # GUCs differ, and later ORM lazy reloads could land on one of them.
        worker_engine = create_engine(
            db.engine.url, pool_size=worker_count, max_overflow=0
        )
        session_factory = sessionmaker(bind=worker_engine)

        def worker(index: int):
            session = session_factory()
            try:
                # Each worker gets its own Session + connection. The RLS GUC
                # must be transaction-local (true) so it never leaks onto the
                # pooled connection for later tests (FORCE RLS is enforced on
                # the counters anyway). It spans the whole worker transaction
                # and is cleared by the final commit().
                session.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": tenant_id},
                )
                session.execute(
                    text("SELECT set_config('app.bypass_rls', 'false', true)")
                )
                barrier.wait(timeout=30)
                case_nums[index].append(
                    allocate_case_number(tenant_id, session=session)
                )
                seqs[index].append(
                    allocate_investigation_sequence_no(
                        tenant_id, case_id, session=session
                    )
                )
                session.commit()
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                session.close()

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
        nums = sorted(int(n[0].split("-")[1]) for n in case_nums if n)
        assert nums == list(range(1, worker_count + 1))
        seqs_flat = sorted(s[0] for s in seqs if s)
        assert seqs_flat == list(range(1, worker_count + 1))
        worker_engine.dispose()

    def test_case_number_immutable_under_rls_bypass(self, app):
        """Reference numbers are immutable at the DB level — an ORM write
        fails even under a full RLS bypass (ADR-0002 D4)."""
        from sqlalchemy.exc import IntegrityError

        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id
        set_tenant_context(db, tenant_id, bypass_rls=True)
        case = self._seed_case(admin, tenant_id)
        case_id = case.id
        original = case.case_number
        db.session.commit()

        case = db.session.get(Case, case_id)
        case.case_number = original[:-1] + "9"
        with pytest.raises(IntegrityError, match="immutable"):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(Case, case_id).case_number == original

    def test_investigation_sequence_no_immutable_under_rls_bypass(self, app):
        from sqlalchemy.exc import IntegrityError

        from cms.services.sequence_service import create_investigation

        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id
        set_tenant_context(db, tenant_id, bypass_rls=True)
        case = self._seed_case(admin, tenant_id)
        created = create_investigation(
            tenant_id=tenant_id, case_id=case.id, title="PG onwijzigbaar"
        )
        db.session.commit()
        inv_id = created.id
        inv = db.session.get(Investigation, inv_id)
        inv.sequence_no = 99
        with pytest.raises(IntegrityError, match="immutable"):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(Investigation, inv_id).sequence_no == 1

    def test_investigation_case_id_immutable_under_rls_bypass(self, app):
        """P1: repointing an investigation to another case is rejected by the
        DB-level identity trigger, even under a full RLS bypass."""
        from sqlalchemy.exc import IntegrityError

        from cms.services.sequence_service import create_investigation

        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id
        set_tenant_context(db, tenant_id, bypass_rls=True)
        case_a = self._seed_case(admin, tenant_id)
        case_b = self._seed_case(admin, tenant_id)
        created = create_investigation(
            tenant_id=tenant_id, case_id=case_a.id, title="PG verplaatsen"
        )
        db.session.commit()
        inv_id = created.id

        inv = db.session.get(Investigation, inv_id)
        inv.case_id = case_b.id
        with pytest.raises(IntegrityError, match="case_id"):
            db.session.commit()
        db.session.rollback()
        kept = db.session.get(Investigation, inv_id)
        assert kept.case_id == case_a.id
        assert kept.tenant_id == tenant_id
        assert kept.sequence_no == 1

    def test_investigation_tenant_id_immutable_under_rls_bypass(self, app):
        """P1: reassigning an investigation to another tenant is rejected by
        the DB-level identity trigger, even under a full RLS bypass."""
        from sqlalchemy.exc import IntegrityError

        from cms.services.sequence_service import create_investigation

        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id
        tenant_b = Tenant(
            name="PG Inv Tenant C",
            slug=f"pg-inv-c-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        tenant_b_id = tenant_b.id

        set_tenant_context(db, tenant_id, bypass_rls=True)
        case = self._seed_case(admin, tenant_id)
        created = create_investigation(
            tenant_id=tenant_id, case_id=case.id, title="PG overzetten"
        )
        db.session.commit()
        inv_id = created.id

        inv = db.session.get(Investigation, inv_id)
        inv.tenant_id = tenant_b_id
        with pytest.raises(IntegrityError, match="tenant_id"):
            db.session.commit()
        db.session.rollback()
        kept = db.session.get(Investigation, inv_id)
        assert kept.tenant_id == tenant_id
        assert kept.case_id == case.id
        assert kept.sequence_no == 1

    def test_archive_restore_status_transitions_on_postgres(self, app):
        """P1: archive/restore shift the DB-level status column on PG too."""
        from cms.services.sequence_service import create_investigation

        admin = User.query.filter_by(username="admin").one()
        tenant_id = admin.tenant_id
        set_tenant_context(db, tenant_id, bypass_rls=True)
        case = self._seed_case(admin, tenant_id)
        created = create_investigation(
            tenant_id=tenant_id, case_id=case.id, title="PG status"
        )
        db.session.commit()
        inv = db.session.get(Investigation, created.id)

        inv.archived_at = datetime.now(UTC)
        inv.status = "archived"
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Investigation, created.id).status == "archived"

        inv = db.session.get(Investigation, created.id)
        inv.archived_at = None
        inv.status = "open"
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Investigation, created.id).status == "open"

    def test_case_number_counter_requires_existing_tenant(self, app):
        """The counter may only reference a real tenant, also under bypass."""
        from sqlalchemy.exc import IntegrityError

        admin = User.query.filter_by(username="admin").one()
        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        db.session.add(
            CaseNumberCounter(tenant_id=str(uuid.uuid4()), year=2026, next_seq=1)
        )
        with pytest.raises(IntegrityError, match="case_number_counters"):
            db.session.flush()
        db.session.rollback()
