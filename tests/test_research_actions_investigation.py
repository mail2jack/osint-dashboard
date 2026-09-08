"""
ADR-0005 PR-A: research_actions.investigation_id same-case/tenant invariant.

These tests prove the schema-level composite FK
``research_actions(investigation_id, case_id, tenant_id)`` -> the unique
parent key ``investigations(id, case_id, tenant_id)`` really refuses a
mismatch at the database level. The app deliberately runs SQLite without
``PRAGMA foreign_keys`` (service-layer checks are authoritative), so a
dedicated engine with the pragma turned on per connection is used — exactly
the pattern from ``test_investigations_numbering.py`` and
``test_invoice_numbering.py``.

Semantics (ADR-0005 D1/D2/D4):
- ``investigation_id = NULL`` stays valid and means case-wide;
- a non-NULL ``investigation_id`` must reference an investigation of the
  same case AND tenant — otherwise IntegrityError (PostgreSQL 23503).
- Existing case-wide rows are never rewritten (no backfill).
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from cms.models import Case, Client, Investigation, ResearchAction, Tenant, db
from cms.tenant_context import set_tenant_context


def _fk_engine():
    """A SQLite engine with ``PRAGMA foreign_keys=ON`` per connection."""
    engine = create_engine(db.engine.url)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _make_tenant(slug_prefix="ra-t") -> Tenant:
    tenant = Tenant(
        name=f"RA Tenant {uuid.uuid4().hex[:8]}",
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:10]}",
        is_active=True,
        tier="enterprise",
        subscription_status="active",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(tenant)
    db.session.flush()
    return tenant


def _make_case(tenant_id, tag: str) -> Case:
    client = Client(
        tenant_id=tenant_id,
        name=f"RA Client {tag} {uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        tenant_id=tenant_id,
        case_number=f"RA-{tag.upper()}-{uuid.uuid4().hex[:8]}",
        client_id=client.id,
        title=f"RA case {tag} {uuid.uuid4().hex[:6]}",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    return case


def _make_investigation(case: Case, seq: int = 1) -> Investigation:
    inv = Investigation(
        id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.id,
        sequence_no=seq,
        title="RA linked investigation",
    )
    db.session.add(inv)
    db.session.flush()
    return inv


@pytest.fixture
def fk_engine(app):
    engine = _fk_engine()
    yield engine
    engine.dispose()


def _insert_action(fk_engine, *, action_id, case_id, tenant_id, investigation_id=None):
    """Raw insert of a research_actions row on the FK-enforced engine.

    Flags ``created_at``/``updated_at`` explicitly (no server defaults).
    """
    now = datetime.now(timezone.utc).isoformat(" ")
    with fk_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO research_actions "
            "(id, tenant_id, case_id, investigation_id, action_type, status, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'google_dork', 'pending', ?, ?)",
            (action_id, tenant_id, case_id, investigation_id, now, now),
        )


class TestResearchActionInvestigationLink:
    def test_model_column_and_to_dict(self, app):
        assert hasattr(ResearchAction, "investigation_id")
        admin_tenant_id = db.session.execute(
            db.text("SELECT tenant_id FROM users WHERE username='admin'")
        ).scalar()
        case = _make_case(admin_tenant_id, "dict")
        db.session.commit()
        action = ResearchAction(
            case_id=case.id,
            action_type="google_dork",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()
        payload = action.to_dict()
        assert "investigation_id" in payload
        assert payload["investigation_id"] is None

    def test_null_investigation_stays_case_wide(self, app, fk_engine):
        tenant_a = _make_tenant()
        case_a = _make_case(tenant_a.id, "a")
        db.session.commit()
        _insert_action(
            fk_engine,
            action_id="ra-null",
            case_id=case_a.id,
            tenant_id=tenant_a.id,
            investigation_id=None,
        )

    def test_same_case_same_tenant_link_ok(self, app, fk_engine):
        tenant_a = _make_tenant()
        case_a = _make_case(tenant_a.id, "a")
        inv_a = _make_investigation(case_a)
        db.session.commit()
        _insert_action(
            fk_engine,
            action_id="ra-ok",
            case_id=case_a.id,
            tenant_id=tenant_a.id,
            investigation_id=inv_a.id,
        )

    def test_cross_case_investigation_rejected(self, app, fk_engine):
        """An investigation from another case of the same tenant is refused."""
        tenant_a = _make_tenant()
        case_a = _make_case(tenant_a.id, "a")
        case_b = _make_case(tenant_a.id, "b")
        inv_b = _make_investigation(case_b)
        db.session.commit()
        with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
            _insert_action(
                fk_engine,
                action_id="ra-cross-case",
                case_id=case_a.id,
                tenant_id=tenant_a.id,
                investigation_id=inv_b.id,
            )

    def test_cross_tenant_investigation_rejected(self, app, fk_engine):
        """An investigation from another tenant (and case) is refused even
        when the action's own case/tenant exist and are FK-valid."""
        tenant_a = _make_tenant()
        tenant_b = _make_tenant(slug_prefix="ra-tb")
        case_a = _make_case(tenant_a.id, "a")
        case_bt = _make_case(tenant_b.id, "bt")
        inv_bt = _make_investigation(case_bt)
        db.session.commit()
        with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
            _insert_action(
                fk_engine,
                action_id="ra-cross-tenant",
                case_id=case_a.id,
                tenant_id=tenant_a.id,
                investigation_id=inv_bt.id,
            )

    def test_existing_case_wide_action_with_bypass_context_untouched(
        self, app, fk_engine
    ):
        """A case-wide action (NULL) stays valid and readable — including when
        the RLS bypass context is set, mirroring how migration-like writes run.
        The composite FK never fires for NULL (MATCH SIMPLE), so no rewrite is
        needed and none happens (no backfill)."""
        tenant_a = _make_tenant()
        case_a = _make_case(tenant_a.id, "a")
        inv_a = _make_investigation(case_a)
        db.session.commit()
        set_tenant_context(db, None, bypass_rls=True)
        _insert_action(
            fk_engine,
            action_id="ra-bypass-null",
            case_id=case_a.id,
            tenant_id=tenant_a.id,
            investigation_id=None,
        )
        _insert_action(
            fk_engine,
            action_id="ra-bypass-linked",
            case_id=case_a.id,
            tenant_id=tenant_a.id,
            investigation_id=inv_a.id,
        )
        # The linked row above only makes sense when it points at the proper
        # parent; a write bypassing RLS is still stopped for a mismatch:
        case_b = _make_case(tenant_a.id, "b")
        inv_b = _make_investigation(case_b)
        db.session.commit()
        with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
            _insert_action(
                fk_engine,
                action_id="ra-bypass-cross",
                case_id=case_a.id,
                tenant_id=tenant_a.id,
                investigation_id=inv_b.id,
            )