"""PostgreSQL/RLS isolation tests for finding-mutation guards.

Proves, on real PostgreSQL under FORCE RLS, that:
  - a different-tenant user cannot mutate (404, not 403) — RLS hides the row;
  - a same-tenant user can mutate its own findings.

Runs only when ``DATABASE_URL`` points at PostgreSQL (the CI
``integration-postgres`` job); skipped elsewhere.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest

from cms.models import (
    Case,
    Client as CmsClient,
    Finding,
    Subject,
    Tenant,
    User,
    db,
)
from cms.tenant_context import set_tenant_context

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PG/RLS isolation requires a real PostgreSQL database.",
)


def _make_user(role, tenant_id, username=None):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=username or f"pg_{token}",
        email=f"pg_{token}@localhost",
        full_name="PG Guard User",
        role=role,
        is_active=True,
    )
    user.tenant_id = tenant_id
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.flush()
    return user


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"


def _seed(app, tenant_id):
    """Seed a case + finding under *tenant_id*, return ``(case_id, finding_id)``."""
    set_tenant_context(db, tenant_id, bypass_rls=True)
    admin = User.query.filter_by(role="admin").first()
    c = CmsClient(name="PG Guard Client", is_active=True)
    db.session.add(c)
    db.session.flush()
    case = Case(
        case_number=f"PG-{uuid.uuid4().hex[:8].upper()}",
        client_id=c.id,
        title="PG Guard Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.flush()
    case.created_by = admin.id
    subj = Subject(
        tenant_id=tenant_id,
        name="PG Subject",
        subject_type="person",
        email="pg-guard@example.com",
    )
    subj.encrypt_identifiers()
    db.session.add(subj)
    db.session.flush()
    finding = Finding(
        tenant_id=tenant_id,
        case_id=case.id,
        subject_id=subj.id,
        title="PG Guard Finding",
        content="PG guard evidence",
        source_type="manual",
        status="candidate",
        verified=False,
        created_by=admin.id,
    )
    db.session.add(finding)
    db.session.commit()
    return case.id, finding.id


class TestFindingGuardRLSIsolation:
    """Cross-tenant isolation via FORCE RLS: row is invisible → 404."""

    def test_cross_tenant_verify_404(self, app):
        admin = User.query.filter_by(role="admin").first()
        tid_a = admin.tenant_id
        case_id, finding_id = _seed(app, tid_a)

        other_tenant = Tenant(
            name=f"RLS-{uuid.uuid4().hex[:8]}",
            slug=f"rls-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(other_tenant)
        db.session.flush()
        user_b = _make_user("investigator", other_tenant.id)
        db.session.commit()

        with app.test_client() as client:
            _login_as(client, user_b)
            resp = client.post(
                f"/cms/workflow/api/case/{case_id}/findings/{finding_id}/verify",
                json={"status": "verified"},
            )
            assert resp.status_code == 404

    def test_cross_tenant_archive_404(self, app):
        admin = User.query.filter_by(role="admin").first()
        tid_a = admin.tenant_id
        case_id, finding_id = _seed(app, tid_a)

        other_tenant = Tenant(
            name=f"RLS-{uuid.uuid4().hex[:8]}",
            slug=f"rls-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(other_tenant)
        db.session.flush()
        user_b = _make_user("investigator", other_tenant.id)
        db.session.commit()

        with app.test_client() as client:
            _login_as(client, user_b)
            resp = client.post(
                f"/cms/workflow/api/findings/{finding_id}/archive",
                json={},
            )
            assert resp.status_code == 404

    def test_own_tenant_verify_ok(self, app):
        admin = User.query.filter_by(role="admin").first()
        tid_a = admin.tenant_id
        case_id, finding_id = _seed(app, tid_a)

        with app.test_client() as client:
            _login_as(client, admin)
            resp = client.post(
                f"/cms/workflow/api/case/{case_id}/findings/{finding_id}/verify",
                json={"status": "verified"},
            )
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_own_tenant_archive_ok(self, app):
        admin = User.query.filter_by(role="admin").first()
        tid_a = admin.tenant_id
        case_id, finding_id = _seed(app, tid_a)

        with app.test_client() as client:
            _login_as(client, admin)
            resp = client.post(
                f"/cms/workflow/api/findings/{finding_id}/archive",
                json={},
            )
            assert resp.status_code == 200
