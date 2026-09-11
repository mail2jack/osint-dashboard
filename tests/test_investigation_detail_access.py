"""
Investigation detail workspace — PR1 (ADR-0002/ADR-0005).

Read-only detail route + central ``ensure_investigation_access`` gate
(identity, case-binding, case-access) + feature-flagged navigation.

    flag OFF → 404 (HTML) en kaarten tonen géén link;
    flag ON  → 200 en kaarten zijn klikbaar.

Run on SQLite (default ``tests/`` suite). PostgreSQL/RLS isolation lives in
``tests/test_postgres_investigation_detail_rls.py``.
"""

import uuid
from datetime import UTC, datetime

from cms.models import (
    Case,
    Client,
    FeatureFlag,
    Investigation,
    User,
    db,
)

WORKSPACE_FLAG = "investigation_workspace"


def _enable_workspace(tenant_id, enabled=True):
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
    return flag


def _make_user(role, tenant_id=None, username=None):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=username or f"det_{token}",
        email=f"det_{token}@localhost",
        full_name="Detail Test User",
        role=role,
        is_active=True,
    )
    if tenant_id:
        user.tenant_id = tenant_id
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.commit()
    return user


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


def _admin_tenant_id():
    return User.query.filter_by(username="admin").first().tenant_id


def _make_client_and_case():
    client = Client(name="Inv Detail Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="Inv Detail Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.commit()
    return client.id, case.id


def _make_investigation(case, title="Detail onderzoek", **kwargs):
    kwargs.setdefault("sequence_no", 1)
    kwargs.setdefault("status", "open")
    inv = Investigation(
        tenant_id=case.tenant_id,
        case_id=case.id,
        title=title,
        **kwargs,
    )
    db.session.add(inv)
    db.session.commit()
    return inv


def _detail_url(case_id, investigation_id):
    return f"/cms/workflow/case/{case_id}/investigations/{investigation_id}"


class TestDetailFlagGate:
    def test_flag_off_returns_404(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 404

    def test_default_off_without_feature_flag(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 404

    def test_flag_on_returns_200(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(
            case,
            title="Bekijk-onderzoek",
            instructions="Bekijk eerst bron X.",
            notes="Interne notitie.",
        )
        resp = auth_client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert inv.human_number in body
        assert "Bekijk-onderzoek" in body
        assert "Bekijk eerst bron X." in body
        assert "Interne notitie." in body
        assert f'/cms/workflow/case/{case_id}' in body

    def test_archived_investigation_visible_when_on(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(
            case, title="Archived detail", archived_at=datetime.now(UTC)
        )
        inv.status = "archived"
        db.session.commit()
        resp = auth_client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200
        assert "Archived".encode() in resp.data


class TestDetailAuthMatrix:
    def test_admin_reads(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200

    def test_investigator_with_case_access_reads(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        investigator = _make_user("investigator", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.investigators.append(investigator)
        db.session.commit()
        inv = _make_investigation(case)
        client = _login_as(app.test_client(), investigator)
        resp = client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200

    def test_senior_investigator_with_case_access_reads(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        senior = _make_user("senior_investigator", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.investigators.append(senior)
        db.session.commit()
        inv = _make_investigation(case)
        client = _login_as(app.test_client(), senior)
        resp = client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200

    def test_junior_with_case_access_reads(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        junior = _make_user("junior_investigator", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.investigators.append(junior)
        db.session.commit()
        inv = _make_investigation(case)
        client = _login_as(app.test_client(), junior)
        resp = client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200

    def test_viewer_with_case_access_reads(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        viewer = _make_user("viewer", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.created_by = viewer.id
        db.session.commit()
        inv = _make_investigation(case)
        client = _login_as(app.test_client(), viewer)
        resp = client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 200

    def test_viewer_without_case_access_gets_403(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        viewer = _make_user("viewer", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.created_by = None
        case.lead_investigator_id = None
        case.assigned_to = None
        db.session.commit()
        inv = _make_investigation(case)
        client = _login_as(app.test_client(), viewer)
        resp = client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 403

    def test_other_tenant_never_visible(self, app, auth_client):
        from cms.models import Tenant

        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        tenant_b = Tenant(
            name="Detail-tenant-B",
            slug=f"x-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        other = _make_user("investigator", tenant_id=tenant_b.id)
        client = _login_as(app.test_client(), other)
        resp = client.get(_detail_url(case_id, inv.id))
        assert resp.status_code in (403, 404)
        assert b"Detail onderzoek" not in resp.data

    def test_flag_off_is_404_even_for_admin(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(case_id, inv.id))
        assert resp.status_code == 404


class TestCaseBinding:
    def test_unknown_investigation_404(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        resp = auth_client.get(_detail_url(case_id, str(uuid.uuid4())))
        assert resp.status_code == 404

    def test_wrong_case_returns_404(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_a = _make_client_and_case()
        _, case_b = _make_client_and_case()
        case = db.session.get(Case, case_a)
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(case_b, inv.id))
        assert resp.status_code == 404

    def test_unknown_case_404(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(str(uuid.uuid4()), inv.id))
        assert resp.status_code == 404


class TestNavigationFlag:
    def test_case_detail_cards_linked_when_on(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(f"/cms/workflow/case/{case_id}")
        assert resp.status_code == 200
        assert f"/investigations/{inv.id}" in resp.get_data(as_text=True)

    def test_case_detail_cards_not_linked_when_off(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(f"/cms/workflow/case/{case_id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Detail link pattern: /cms/workflow/case/{case_id}/investigations/{inv.id}
        # (NOT /api/investigations/... which archive/restore forms still use)
        detail_path = f"/cms/workflow/case/{case_id}/investigations/{inv.id}"
        assert detail_path not in html

    def test_standalone_list_linked_when_on(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert resp.status_code == 200
        assert f"/investigations/{inv.id}" in resp.get_data(as_text=True)

    def test_standalone_list_not_linked_when_off(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        detail_path = f"/cms/workflow/case/{case_id}/investigations/{inv.id}"
        assert detail_path not in html