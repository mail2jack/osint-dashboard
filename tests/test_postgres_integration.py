"""Mandatory PostgreSQL integration coverage for tenant isolation."""

import os
from datetime import date
import uuid

import pytest
from sqlalchemy import text

from cms.models import AuditLog, Case, Client, LoginLog, Notification, Tenant, User, db
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
        # Keep this assertion aligned with the current Alembic head.  The
        # following migration adds subject-search indexes after FORCE RLS and
        # the cascade-FK safety migration.
        assert revision == "c3d4e5f6a7b8"

        protected = db.session.execute(
            text(
                """
                SELECT count(*)
                FROM pg_class
                WHERE relname IN (
                    'cases', 'clients', 'notifications',
                    'subject_identifiers', 'subject_facts'
                )
                  AND relrowsecurity
                  AND relforcerowsecurity
                """
            )
        ).scalar()
        assert protected == 5

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
