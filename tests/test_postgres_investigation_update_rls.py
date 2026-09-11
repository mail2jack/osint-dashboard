"""PostgreSQL/RLS isolation tests for the investigation update endpoint (PR2).

The canonical update route resolves its ``Investigation`` under FORCE RLS and
writes through the tenant-enforced policy, so:

  * an owning-tenant investigator can update when the flag is ON;
  * a different-tenant investigator is rejected (0 rows -> 403/404, no leak);
  * a wrong ``(case_id, investigation_id)`` pair returns 404 without mutating;
  * without the tenant GUC the target row is invisible (0 rows -> 404).

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


def _make_case_and_investigation(tenant_id, title="PG-update-onderzoek"):
    set_tenant_context(db, tenant_id, bypass_rls=True)
    client = Client(name="PG Upd Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"PGU-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="PG Upd Case",
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
        instructions="PG-instructies",
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


def _update_url(case_id, investigation_id):
    return f"/cms/workflow/api/case/{case_id}/investigations/{investigation_id}/update"


def _title_in_db(tenant_id, investigation_id):
    set_tenant_context(db, tenant_id, bypass_rls=True)
    db.session.expire_all()
    inv = db.session.get(Investigation, investigation_id)
    title = inv.title if inv else None
    db.session.commit()
    return title


class TestPGInvestigationUpdateIsolation:
    def test_owning_tenant_updates_under_force_rls(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _enable_workspace(tenant_id, enabled=True)
        case_id, investigation_id = _make_case_and_investigation(tenant_id)

        set_tenant_context(db, tenant_id)
        resp = auth_client.post(
            _update_url(case_id, investigation_id),
            json={"title": "PG bijgewerkt", "notes": "PG notitie"},
        )
        assert resp.status_code == 200

        assert _title_in_db(tenant_id, investigation_id) == "PG bijgewerkt"

    def test_other_tenant_update_rejected_no_mutation(self, app):
        from cms.models import Tenant

        admin = User.query.filter_by(username="admin").first()
        tenant_a = admin.tenant_id
        _enable_workspace(tenant_a, enabled=True)
        case_id, investigation_id = _make_case_and_investigation(tenant_a)

        set_tenant_context(db, tenant_a, bypass_rls=True)
        tenant_b = Tenant(
            name="PG Upd B",
            slug=f"y-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        other = User(
            username=f"pgupd_{uuid.uuid4().hex[:8]}",
            email="pgupd@localhost",
            full_name="PG Other Tenant",
            role="investigator",
            tenant_id=tenant_b.id,
            is_active=True,
        )
        other.set_password("Test1234!")
        db.session.add(other)
        db.session.commit()

        client = _login_as(app.test_client(), other)
        resp = client.post(
            _update_url(case_id, investigation_id),
            json={"title": "PG gelekt? Niet dus."},
        )
        assert resp.status_code in (403, 404)
        assert _title_in_db(tenant_a, investigation_id) == "PG-update-onderzoek"

    def test_wrong_case_pair_404_no_mutation(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _enable_workspace(tenant_id, enabled=True)
        case_a, investigation_id = _make_case_and_investigation(tenant_id)
        case_b, _ = _make_case_and_investigation(tenant_id)
        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        set_tenant_context(db, tenant_id)

        resp = auth_client.post(
            _update_url(case_b, investigation_id),
            json={"title": "Verkeerd geval"},
        )
        assert resp.status_code == 404
        assert _title_in_db(tenant_id, investigation_id) == "PG-update-onderzoek"

    def test_without_guc_target_invisible_404(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _enable_workspace(tenant_id, enabled=True)
        case_id, investigation_id = _make_case_and_investigation(tenant_id)

        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        set_tenant_context(db, None)
        resp = auth_client.post(
            _update_url(case_id, investigation_id),
            json={"title": "Ondoordringbaar"},
        )
        assert resp.status_code in (403, 404)

        assert _title_in_db(tenant_id, investigation_id) == "PG-update-onderzoek"