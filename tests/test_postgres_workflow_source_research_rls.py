"""Real PostgreSQL/RLS coverage for workflow-native source research.

The native status endpoint must never expose a SpiderFoot-backed action or
its cached proposals across tenants.  This runs only in the PostgreSQL CI job
where FORCE RLS is active.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest

from cms.models import Case, Client, FeatureFlag, Investigation, Tenant, User, db
from cms.services.workflow_source_research import queue_passive_source_research
from cms.tenant_context import set_tenant_context

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PG/RLS isolation requires a real PostgreSQL database.",
)


def _login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    return client


def _enable(tenant_id, flag_name):
    set_tenant_context(db, tenant_id, bypass_rls=True)
    flag = FeatureFlag.query.filter_by(tenant_id=tenant_id, flag_name=flag_name).first()
    if flag is None:
        db.session.add(FeatureFlag(tenant_id=tenant_id, flag_name=flag_name, enabled=True))
    else:
        flag.enabled = True
    db.session.commit()


def _seed(tenant_id, actor):
    set_tenant_context(db, tenant_id, bypass_rls=True)
    client = Client(name="PG Source Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"PG-SRC-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="PG source-research case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=actor.id,
    )
    db.session.add(case)
    db.session.flush()
    investigation = Investigation(
        tenant_id=tenant_id,
        case_id=case.id,
        sequence_no=1,
        title="PG source-research investigation",
        status="open",
    )
    db.session.add(investigation)
    db.session.flush()
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=actor,
        target_type="domain",
        target_value="example.test",
    )
    action.status = scan.status = "completed"
    scan.result_summary = {"proposals": [{"type": "DOMAIN_NAME", "data": "example.test"}]}
    db.session.commit()
    return case.id, investigation.id, action.id


def test_other_tenant_cannot_read_source_research_status(app):
    set_tenant_context(db, None, bypass_rls=True)
    owner = User.query.filter_by(username="admin").first()
    assert owner is not None
    _enable(owner.tenant_id, "workflow_spiderfoot")
    case_id, investigation_id, action_id = _seed(owner.tenant_id, owner)

    set_tenant_context(db, owner.tenant_id, bypass_rls=True)
    other_tenant = Tenant(
        name=f"PG source other {uuid.uuid4().hex[:8]}",
        slug=f"pg-source-{uuid.uuid4().hex[:8]}",
        is_active=True,
        tier="enterprise",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(other_tenant)
    db.session.flush()
    other = User(
        username=f"pg_source_{uuid.uuid4().hex[:8]}",
        email=f"pg_source_{uuid.uuid4().hex[:8]}@localhost",
        full_name="PG Source Other",
        role="investigator",
        tenant_id=other_tenant.id,
        is_active=True,
    )
    other.set_password("Test1234!")
    db.session.add(other)
    db.session.commit()

    response = _login_as(app.test_client(), other).get(
        "/cms/workflow/api/case/"
        f"{case_id}/investigations/{investigation_id}/source-research/{action_id}"
    )
    assert response.status_code in (403, 404)
    assert b"example.test" not in response.data
