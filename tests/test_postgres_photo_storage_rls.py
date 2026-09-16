"""PostgreSQL/RLS isolation tests for tenant-scoped photo-analysis storage.

Proves, on real PostgreSQL under FORCE RLS, that the photo-analysis worker
path stays tenant-scoped end to end:

  - two tenants each store a photo; tenant A's data_value resolves and
    unlinks only inside tenant A's directory, never tenant B's (even with a
    hand-manipulated ``data_value`` that names tenant B's file);
  - ``sweep_stale_photos`` under an RLS tenant context removes only files in
    the current tenant's directory and never files still referenced by a
    pending/running photo_analysis action (which the RLS-scoped
    ``collect_active_photo_data_values`` returns);
  - the partial unique index
    ``uq_research_actions_active_photo_analysis`` is enforced on PostgreSQL,
    so a concurrent duplicate upload becomes an IntegrityError that the route
    maps to 409;
  - a cold worker (no tenant context) resolving a photo_analysis action still
    runs analysis scoped to the action's tenant and cannot read another
    tenant's row.

Runs only when ``DATABASE_URL`` points at PostgreSQL (the CI
``integration-postgres`` job); skipped elsewhere.
"""

import os
import os.path
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from cms import db
from cms.models import Case, Client, ResearchAction, Tenant, User
from cms.services import photo_storage
from cms.tenant_context import set_tenant_context
from cms.workflow.actions.registry import run_action

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PG/RLS isolation requires a real PostgreSQL database.",
)


def _make_png_bytes():
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00"
        + b"\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01"
        + b"\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )


class _FakeFile:
    def __init__(self, content, content_type="image/png", filename="p.png"):
        import io

        self._buf = io.BytesIO(content)
        self.content_type = content_type
        self.filename = filename

    def tell(self):
        return self._buf.tell()

    def seek(self, pos):
        self._buf.seek(pos)

    def read(self, n=-1):
        return self._buf.read(n)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self._buf.getvalue())


def _new_tenant(name):
    token = uuid.uuid4().hex[:8]
    tenant = Tenant(
        name=f"{name}-{token}",
        slug=f"{name}-{token}".lower(),
        is_active=True,
        tier="enterprise",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(tenant)
    db.session.flush()
    return tenant


def _new_user(role, tenant_id):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=f"pg_rls_{token}",
        email=f"pg_rls_{token}@localhost",
        full_name="PG RLS Photo User",
        role=role,
        is_active=True,
    )
    user.tenant_id = tenant_id
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.flush()
    return user


def _seed_case(tenant_id, case_number_prefix):
    admin = User.query.filter_by(role="admin").first()
    client = Client(name="PG RLS Photo Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"{case_number_prefix}-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="PG RLS Photo Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.flush()
    case.created_by = admin.id
    return case


def _make_action(case, tenant_id, data_value, status="pending"):
    action = ResearchAction(
        case_id=case.id,
        tenant_id=tenant_id,
        action_type="photo_analysis",
        data_value=data_value,
        status=status,
        created_by=User.query.filter_by(role="admin").first().id,
    )
    db.session.add(action)
    db.session.flush()
    return action


def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        photo_storage, "photo_analysis_root", lambda: str(tmp_path / "photo_root")
    )
    return str(tmp_path / "photo_root")


class TestCrossTenantFileIsolation:
    """Tenant-scoped resolve/unlink even under a hostile data_value."""

    def test_resolve_never_crosses_tenants(self, app, tmp_path, monkeypatch):
        root = _root(tmp_path, monkeypatch)
        tid_a = uuid.uuid4().hex
        tid_b = uuid.uuid4().hex
        name_a = photo_storage.store_photo(_FakeFile(_make_png_bytes()), "png", tid_a)

        # Tenant B cannot resolve tenant A's file just by naming it.
        assert photo_storage.resolve_photo_path(name_a, tid_b) is None
        # Tenant A itself resolves it under its own directory.
        resolved = photo_storage.resolve_photo_path(name_a, tid_a)
        assert resolved is not None
        assert os.path.realpath(resolved).startswith(
            os.path.realpath(os.path.join(root, tid_a))
        )

    def test_unlink_never_crosses_tenants(self, app, tmp_path, monkeypatch):
        root = _root(tmp_path, monkeypatch)
        tid_a = uuid.uuid4().hex
        tid_b = uuid.uuid4().hex
        name_a = photo_storage.store_photo(_FakeFile(_make_png_bytes()), "png", tid_a)
        full_a = os.path.join(root, tid_a, name_a)

        # Tenant B's unlink is a no-op; tenant A's file survives.
        assert photo_storage.unlink_photo(name_a, tid_b) is False
        assert os.path.exists(full_a)
        # Tenant A can remove its own file.
        assert photo_storage.unlink_photo(name_a, tid_a) is True
        assert not os.path.exists(full_a)

    def test_worker_action_with_other_tenant_data_value(self, app, tmp_path, monkeypatch):
        """A worker whose action references another tenant's file never sees it."""
        root = _root(tmp_path, monkeypatch)
        tid_a = uuid.uuid4().hex
        tid_b = uuid.uuid4().hex
        name_a = photo_storage.store_photo(_FakeFile(_make_png_bytes()), "png", tid_a)

        set_tenant_context(db, tid_b)
        # Even under tenant B's RLS context the resolver still needs B's file.
        assert photo_storage.resolve_photo_path(name_a, tid_b) is None
        # And A's file is untouched by B's cleanup attempts.
        assert photo_storage.unlink_photo(name_a, tid_b) is False
        assert os.path.exists(os.path.join(root, tid_a, name_a))


class TestSweepUnderRLS:
    def test_sweep_only_removes_unreferenced_own_tenant(
        self, app, tmp_path, monkeypatch
    ):
        root = _root(tmp_path, monkeypatch)
        tid = uuid.uuid4().hex

        # A stale unreferenced file in the tenant's own dir.
        stale_name = photo_storage.store_photo(
            _FakeFile(_make_png_bytes()), "png", tid
        )
        stale_path = os.path.join(root, tid, stale_name)
        old = datetime.now().timestamp() - photo_storage.STALE_FILE_MAX_AGE.total_seconds() * 2
        os.utime(stale_path, (old, old))

        # A freshly referenced file (pending action) must survive.
        referenced_name = photo_storage.store_photo(
            _FakeFile(_make_png_bytes()), "png", tid
        )
        referenced_path = os.path.join(root, tid, referenced_name)

        # A tenant B file (by a different tenant id) must never be touched.
        other_tid = uuid.uuid4().hex
        other_name = photo_storage.store_photo(
            _FakeFile(_make_png_bytes()), "png", other_tid
        )
        other_path = os.path.join(root, other_tid, other_name)
        old_other = (
            datetime.now().timestamp()
            - photo_storage.STALE_FILE_MAX_AGE.total_seconds() * 2
        )
        os.utime(other_path, (old_other, old_other))

        removed = photo_storage.sweep_stale_photos(
            tid, referenced={referenced_name}
        )

        assert removed == 1
        assert not os.path.exists(stale_path)
        assert os.path.exists(referenced_path)
        assert os.path.exists(other_path)

    def test_collect_active_values_runs_in_tenant_context(
        self, app, tmp_path, monkeypatch
    ):
        _root(tmp_path, monkeypatch)
        tid = uuid.uuid4().hex
        case = _seed_case(tid, "PG-RLS")
        action = _make_action(case, tid, "active.png", status="running")
        db.session.commit()

        set_tenant_context(db, tid)
        active = photo_storage.collect_active_photo_data_values()
        assert "active.png" in active

        # A different tenant (RLS hides A's rows) sees no photo actions.
        other_tid = uuid.uuid4().hex
        set_tenant_context(db, other_tid)
        db.session.expire_all()
        assert photo_storage.collect_active_photo_data_values() == set()
        assert ResearchAction.query.filter_by(id=action.id).count() == 0

    def test_sweep_never_removes_file_referenced_in_other_tenant(
        self, app, tmp_path, monkeypatch
    ):
        root = _root(tmp_path, monkeypatch)
        tid_a = uuid.uuid4().hex
        tid_b = uuid.uuid4().hex
        name_b = photo_storage.store_photo(_FakeFile(_make_png_bytes()), "png", tid_b)
        full_b = os.path.join(root, tid_b, name_b)
        old = (
            datetime.now().timestamp()
            - photo_storage.STALE_FILE_MAX_AGE.total_seconds() * 2
        )
        os.utime(full_b, (old, old))

        # Tenant A sweeps its own dir only; B's file (even stale) survives.
        assert photo_storage.sweep_stale_photos(tid_a) == 0
        assert os.path.exists(full_b)


class TestPartialUniqueIndexOnPostgres:
    def test_duplicate_preflight_visibility_requires_explicit_bypass(self, app):
        """FORCE RLS must not turn an unscoped inventory into valid zero rows."""
        admin = User.query.filter_by(role="admin").first()
        set_tenant_context(db, admin.tenant_id, bypass_rls=True)
        case = _seed_case(admin.tenant_id, "PG-RLS-VIS")
        _make_action(case, admin.tenant_id, "visible.png", status="pending")
        db.session.commit()

        set_tenant_context(db, None)
        db.session.expire_all()
        hidden = db.session.execute(
            text(
                "SELECT COUNT(*) FROM research_actions "
                "WHERE action_type = 'photo_analysis' "
                "AND status IN ('pending', 'running') "
                "AND archived_at IS NULL"
            )
        ).scalar_one()
        assert hidden == 0

        db.session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        visible = db.session.execute(
            text(
                "SELECT COUNT(*) FROM research_actions "
                "WHERE action_type = 'photo_analysis' "
                "AND status IN ('pending', 'running') "
                "AND archived_at IS NULL"
            )
        ).scalar_one()
        assert visible == 1

    def test_index_exists(self, app):
        row = db.session.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE indexname = 'uq_research_actions_active_photo_analysis'"
            )
        ).first()
        assert row is not None

    def test_index_predicate_excludes_archived_actions(self, app):
        predicate = db.session.execute(
            text(
                "SELECT pg_get_expr(i.indpred, i.indrelid) "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_research_actions_active_photo_analysis'"
            )
        ).scalar_one()
        assert "archived_at IS NULL" in predicate

    def test_duplicate_active_row_is_integrity_error(self, app):
        tid = uuid.uuid4().hex
        case = _seed_case(tid, "PG-RLS-IDX")
        a = _make_action(case, tid, "a.png", status="pending")
        db.session.commit()

        b = ResearchAction(
            case_id=case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="b.png",
            status="pending",
            created_by=a.created_by,
        )
        db.session.add(b)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_different_tenants_can_both_be_active(self, app):
        tid_a = uuid.uuid4().hex
        tid_b = uuid.uuid4().hex
        case_a = _seed_case(tid_a, "PG-RLS-A")
        case_b = _seed_case(tid_b, "PG-RLS-B")
        _make_action(case_a, tid_a, "a.png", status="pending")
        _make_action(case_b, tid_b, "b.png", status="pending")
        db.session.commit()

    def test_completed_does_not_block_new_active(self, app):
        tid = uuid.uuid4().hex
        case = _seed_case(tid, "PG-RLS-C")
        _make_action(case, tid, "a.png", status="completed")
        db.session.commit()
        _make_action(case, tid, "b.png", status="pending")
        db.session.commit()


class TestColdWorkerPhotoAnalysis:
    def test_cold_worker_runs_scoped_and_unlinks_file(
        self, app, tmp_path, monkeypatch
    ):
        from unittest.mock import patch

        root = _root(tmp_path, monkeypatch)
        tenant = _new_tenant("PhotoRls")
        db.session.commit()

        set_tenant_context(db, tenant.id, bypass_rls=True)
        case = _seed_case(tenant.id, "PG-RLS-COLD")
        name = photo_storage.store_photo(_FakeFile(_make_png_bytes()), "png", tenant.id)
        action = _make_action(case, tenant.id, name, status="pending")
        db.session.commit()
        action_id = str(action.id)
        db.session.expire_all()

        # Cold worker: NO tenant context at all.
        set_tenant_context(db, None)
        db.session.expire_all()

        with (
            patch(
                "cms.services.photo_analysis.analyze_photo",
                return_value={
                    "gps": {"lat": 1, "lng": 2},
                    "camera": {},
                    "datetime": None,
                    "software": None,
                    "privacy": "lower",
                },
            ),
            patch(
                "cms.services.photo_analysis.format_analysis_finding",
                return_value={
                    "title": "ok",
                    "source_type": "photo_analysis",
                    "verified": False,
                    "raw_data": {"gps": {"lat": 1, "lng": 2}},
                },
            ),
            patch(
                "cms.workflow.actions.other_action._action_subject",
                return_value=None,
            ),
        ):
            run_action(action_id)

        # File removed after analysis (finally-cleanup).
        assert not os.path.exists(os.path.join(root, tenant.id, name))

        set_tenant_context(db, tenant.id)
        db.session.expire_all()
        reloaded = db.session.get(ResearchAction, action_id)
        assert reloaded.status == "completed"

        # The worker ran _photo_analysis; file was consumed exactly once.
        assert photo_storage.resolve_photo_path(name, tenant.id) is None

    def test_cold_worker_cannot_see_other_tenant_action(self, app):
        tid_a = uuid.uuid4().hex
        tid_b = uuid.uuid4().hex
        case = _seed_case(tid_a, "PG-RLS-ISO")
        action = _make_action(case, tid_a, "x.png", status="pending")
        db.session.commit()
        action_id = str(action.id)

        set_tenant_context(db, tid_b)
        db.session.expire_all()
        # Tenant B sees no A rows under FORCE RLS.
        assert ResearchAction.query.filter_by(id=action_id).count() == 0
