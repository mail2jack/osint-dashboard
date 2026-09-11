"""PostgreSQL/RLS isolation tests for the investigation detail workspace (PR1).

The detail route / ``ensure_investigation_access`` runs its ``Investigation``
query under FORCE RLS, so without the tenant GUC the row is invisible and the
route aborts 404. This file proves, on real PostgreSQL:

  * an owning-tenant user sees the detail when the flag is ON;
  * a different-tenant user never sees it (RLS returns 0 rows -> 404);
  * a wrong ``(case_id, investigation_id)`` pair returns 404 (case-binding);
  * without the tenant GUC the row is invisible (0 rows).

Runs only when ``DATABASE_URL`` points at PostgreSQL (the CI
``integration-postgres`` job); skipped elsewhere.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest

from cms.models import Case, Client, FeatureFlag, Investigation, User, db
from cms.tenant_context import set_tenant_context

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PG/RLS isolation requires a real PostgreSQL database.",
)

WORKSPACE_FLAG = "investigation_workspace"


def _enable_workspace(tenant_id, enabled=True):
    set_tenant_context(db, tenant_id, bypass_rls=True)
    flag = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name=WORKSPACE_FLAG
    ).first()
    if flag:
        flag.enabled = enabled
    else:
        flag = FeatureFlag(
            tenant_id=tenant_id, flag_name=WORKSPACE_FLAG, enabled=enabled
        )
        db.session.add(flag)
    db.session.commit()


def _make_case_and_investigation(tenant_id, title="PG-detail-onderzoek"):
    set_tenant_context(db, tenant_id, bypass_rls=True)
    client = Client(name="PG Det Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"PG-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="PG Det Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.flush()
    investigation = Investigation(
        tenant_id=tenant_id,
        case_id=case.id,
        sequence_no=1,
        title=title,
        status="open",
    )
    db.session.add(investigation)
    db.session.commit()
    return case.id, investigation.id


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


class TestPGInvestigationDetailIsolation:
    def test_owning_tenant_sees_detail_when_on(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _enable_workspace(tenant_id, enabled=True)
        case_id, investigation_id = _make_case_and_investigation(tenant_id)

        # Simulate the authenticated request path (GUC = tenant, no bypass).
        set_tenant_context(db, tenant_id)
        resp = auth_client.get(
            f"/cms/workflow/case/{case_id}/investigations/{investigation_id}"
        )
        assert resp.status_code == 200
        assert b"PG-detail-onderzoek" in resp.data

    def test_other_tenant_never_visible_under_force_rls(self, app, auth_client):
        from cms.models import Tenant

        admin = User.query.filter_by(username="admin").first()
        tenant_a = admin.tenant_id
        _enable_workspace(tenant_a, enabled=True)
        case_id, investigation_id = _make_case_and_investigation(tenant_a)

        set_tenant_context(db, tenant_a, bypass_rls=True)
        tenant_b = Tenant(
            name="PG Det B",
            slug=f"x-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        other = User(
            username=f"pgother_{uuid.uuid4().hex[:8]}",
            email="pgother@localhost",
            full_name="PG Other Tenant",
            role="investigator",
            tenant_id=tenant_b.id,
            is_active=True,
        )
        other.set_password("Test1234!")
        db.session.add(other)
        db.session.commit()

        client = _login_as(app.test_client(), other)
        resp = client.get(
            f"/cms/workflow/case/{case_id}/investigations/{investigation_id}"
        )
        # RLS makes the investigation invisible -> 404, never a data leak.
        assert resp.status_code in (403, 404)
        assert b"PG-detail-onderzoek" not in resp.data

    def test_without_guc_investigation_invisible(self, app):
        """Without the tenant GUC, FORCE RLS returns zero rows for the query."""
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _, investigation_id = _make_case_and_investigation(tenant_id)

        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        db.session.commit()
        # Clear the GUC: no tenant -> RLS hides every row.
        set_tenant_context(db, None)
        from cms.models import Investigation

        rows = Investigation.query.filter_by(id=investigation_id).count()
        assert rows == 0

        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        set_tenant_context(db, tenant_id)
        rows = Investigation.query.filter_by(id=investigation_id).count()
        assert rows == 1

    def test_wrong_case_returns_404(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _enable_workspace(tenant_id, enabled=True)
        case_a, investigation_id = _make_case_and_investigation(tenant_id)
        case_b, _ = _make_case_and_investigation(tenant_id)
        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        set_tenant_context(db, tenant_id)

        # Investigation belongs to case_a; addressing it via case_b must 404.
        resp = auth_client.get(
            f"/cms/workflow/case/{case_b}/investigations/{investigation_id}"
        )
        assert resp.status_code == 404