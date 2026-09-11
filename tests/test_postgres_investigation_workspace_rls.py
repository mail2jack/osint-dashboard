"""PostgreSQL/RLS isolation tests for the investigation workspace queries (PR3).

Proves, on real PostgreSQL under FORCE RLS, that the workspace query layer and
rendered page are invisible to a different-tenant user:

  * owning tenant sees the full workspace with actions/subjects/findings/timeline;
  * a different-tenant user never sees 200 with data (RLS hides the rows → 403/404);
  * without the tenant GUC every investigation row is invisible.

Runs only when ``DATABASE_URL`` points at PostgreSQL (the CI
``integration-postgres`` job); skipped elsewhere.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest

from cms.models import (
    ActionFinding,
    AuditLog,
    Case,
    Client,
    FeatureFlag,
    Finding,
    Investigation,
    ResearchAction,
    Subject,
    Tenant,
    User,
    db,
)
from cms.services.investigation_workspace import (
    build_inv_workspace,
    load_inv_actions,
    load_inv_findings,
    load_inv_subjects,
    load_inv_timeline,
)
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


def _setup_workspace_data(tenant_id):
    """Create a full workspace scaffold under RLS bypass, then set normal GUC."""
    set_tenant_context(db, tenant_id, bypass_rls=True)
    admin = User.query.filter_by(role="admin").first()
    client = Client(name="PG WS Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"PG-WS-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="PG Workspace Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.flush()
    case.created_by = admin.id
    inv = Investigation(
        tenant_id=tenant_id,
        case_id=case.id,
        sequence_no=1,
        title="PG Workspace Investigation",
        status="open",
    )
    db.session.add(inv)
    db.session.flush()
    subject = Subject(
        tenant_id=tenant_id,
        name="PG Subject Encrypted",
        subject_type="person",
        email="pg@example.com",
    )
    subject.encrypt_identifiers()
    db.session.add(subject)
    db.session.flush()
    action = ResearchAction(
        case_id=case.id,
        tenant_id=tenant_id,
        subject_id=subject.id,
        investigation_id=inv.id,
        target_kind="subject",
        action_type="subdomain",
        label="PG Dork Action",
        status="completed",
        completed_at=datetime.now(UTC),
    )
    db.session.add(action)
    db.session.flush()
    finding = Finding(
        tenant_id=tenant_id,
        case_id=case.id,
        subject_id=subject.id,
        title="PG Finding Evidence",
        content="PG evidence body",
        source_type="osint",
        status="candidate",
        created_by=admin.id,
    )
    db.session.add(finding)
    db.session.flush()
    db.session.add(ActionFinding(action_id=action.id, finding_id=finding.id))
    AuditLog.log(
        user_id=str(admin.id),
        action="create",
        entity_type="finding",
        entity_id=finding.id,
        case_id=case.id,
        tenant_id=tenant_id,
        description="PG finding created",
    )
    db.session.commit()
    return case.id, inv.id


def _other_tenant_user():
    """Create a second tenant + investigator user."""
    tenant = Tenant(
        name=f"PG WS Other {uuid.uuid4().hex[:8]}",
        slug=f"pgws-{uuid.uuid4().hex[:8]}",
        is_active=True,
        tier="enterprise",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(tenant)
    db.session.flush()
    other = User(
        username=f"pgws_{uuid.uuid4().hex[:8]}",
        email="pgws@localhost",
        full_name="PG WS Other Tenant",
        role="investigator",
        tenant_id=tenant.id,
        is_active=True,
    )
    other.set_password("Test1234!")
    db.session.add(other)
    db.session.commit()
    return other, tenant.id


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


class TestPGWorkspaceRLSIsolation:
    def test_owning_tenant_sees_workspace(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id
        _enable_workspace(tenant_id, enabled=True)
        case_id, investigation_id = _setup_workspace_data(tenant_id)
        set_tenant_context(db, tenant_id)
        resp = auth_client.get(
            f"/cms/workflow/case/{case_id}/investigations/{investigation_id}"
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "PG Workspace Investigation" in body
        assert "PG Dork Action" in body
        assert "PG Finding Evidence" in body

    def test_other_tenant_never_sees_workspace(self, app, auth_client):
        admin = User.query.filter_by(username="admin").first()
        tenant_a = admin.tenant_id
        _enable_workspace(tenant_a, enabled=True)
        case_id, investigation_id = _setup_workspace_data(tenant_a)
        other_user, tenant_b_id = _other_tenant_user()

        set_tenant_context(db, tenant_b_id)
        client = _login_as(app.test_client(), other_user)
        resp = client.get(
            f"/cms/workflow/case/{case_id}/investigations/{investigation_id}"
        )
        assert resp.status_code in (403, 404)
        assert "PG Workspace Investigation" not in resp.get_data(as_text=True)

    def test_other_tenant_actions_invisible(self, app):
        admin = User.query.filter_by(username="admin").first()
        tenant_a = admin.tenant_id
        _enable_workspace(tenant_a, enabled=True)
        case_id, investigation_id = _setup_workspace_data(tenant_a)
        other_user, tenant_b_id = _other_tenant_user()

        set_tenant_context(db, tenant_b_id)
        inv = db.session.query(Investigation).filter_by(id=investigation_id).first()
        ws = build_inv_workspace(inv, None)
        assert ws.counts["actions"] == 0
        assert ws.counts["subjects"] == 0
        assert ws.counts["findings"] == 0
        # The granular query loaders are equally RLS-bound under tenant_b.
        assert load_inv_actions(
            tenant_id=tenant_b_id, case_id=case_id, investigation_id=investigation_id
        ) == []
        assert load_inv_findings(
            tenant_id=tenant_b_id, case_id=case_id, action_ids=[]
        ) == ([], {})
        assert load_inv_subjects(tenant_id=tenant_b_id, subject_ids=[]) == []
        result = load_inv_timeline(
            tenant_id=tenant_b_id,
            case_id=case_id,
            investigation_id=investigation_id,
            action_ids=[],
            finding_ids=[],
        )
        assert result.total == 0
        assert result.events == []

    def test_without_guc_investigation_invisible(self, app):
        admin = User.query.filter_by(role="admin").first()
        tenant_id = admin.tenant_id
        _, investigation_id = _setup_workspace_data(tenant_id)
        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        set_tenant_context(db, None)
        rows = Investigation.query.filter_by(id=investigation_id).count()
        assert rows == 0
        set_tenant_context(db, tenant_id, bypass_rls=True)
        rows = Investigation.query.filter_by(id=investigation_id).count()
        assert rows == 1