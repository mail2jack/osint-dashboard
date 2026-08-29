"""
Investigations screen (ADR-0002) — create/list/archive/restore routes, case &
tenant isolation, viewer read-only access, audit logging.

Run on SQLite (part of the default ``tests/`` suite).
"""

import uuid
from datetime import UTC, datetime

from cms.models import AuditLog, Case, Client, Investigation, User, db


def _make_user(role, tenant_id=None, username=None):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=username or f"invtest_{token}",
        email=f"invtest_{token}@localhost",
        full_name="Investigation Test User",
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


def _make_client_and_case():
    client = Client(name="Inv Screen Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="Investigation Screen Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.commit()
    return client.id, case.id


def _admin_tenant_id():
    return User.query.filter_by(username="admin").first().tenant_id


def _create_inv(client, case_id, data):
    return client.post(
        f"/cms/workflow/api/case/{case_id}/investigations", data=data
    )


class TestCreateInvestigation:
    def test_create_via_html_form(self, app, auth_client):
        _, case_id = _make_client_and_case()
        resp = _create_inv(auth_client, case_id, {"title": "Onderzoek A"})
        assert resp.status_code == 302
        inv = Investigation.query.filter_by(case_id=case_id).one()
        assert inv.sequence_no == 1
        assert inv.human_number == f"{inv.case.case_number}-01"
        assert inv.status == "open"
        assert inv.created_by == User.query.filter_by(username="admin").first().id
        assert inv.archived_at is None

    def test_create_via_json(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case_id}/investigations",
            json={"title": "Onderzoek JSON", "instructions": "instr", "notes": "noot"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["investigation"]["human_number"] == f"{case.case_number}-01"
        assert body["investigation"]["title"] == "Onderzoek JSON"
        inv = Investigation.query.filter_by(case_id=case_id).one()
        assert inv.instructions == "instr"
        assert inv.notes == "noot"

    def test_submitted_sequence_and_number_are_ignored(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        resp = _create_inv(
            auth_client,
            case_id,
            {
                "title": "Hack attempt",
                "sequence_no": "999",
                "human_number": "X-999-999",
            },
        )
        assert resp.status_code == 302
        inv = Investigation.query.filter_by(case_id=case_id).one()
        assert inv.sequence_no == 1
        assert inv.human_number == f"{case.case_number}-01"

    def test_title_required_json(self, app, auth_client):
        _, case_id = _make_client_and_case()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case_id}/investigations", json={}
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Title is required"

    def test_title_required_html(self, app, auth_client):
        _, case_id = _make_client_and_case()
        resp = _create_inv(auth_client, case_id, {})
        assert resp.status_code == 302
        assert Investigation.query.filter_by(case_id=case_id).count() == 0

    def test_sequential_numbering(self, app, auth_client):
        _, case_id = _make_client_and_case()
        _create_inv(auth_client, case_id, {"title": "Eerst"})
        _create_inv(auth_client, case_id, {"title": "Tweede"})
        invs = (
            Investigation.query.filter_by(case_id=case_id)
            .order_by(Investigation.sequence_no)
            .all()
        )
        assert [i.sequence_no for i in invs] == [1, 2]
        assert invs[1].human_number.endswith("-02")

    def test_unknown_case_404_json(self, app, auth_client):
        resp = _create_inv(auth_client, str(uuid.uuid4()), {"title": "X"})
        assert resp.status_code == 404


class TestListInvestigations:
    def test_case_detail_shows_investigations(self, app, auth_client):
        client_id, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        gov = Investigation(
            tenant_id=case.tenant_id,
            case_id=case_id,
            sequence_no=1,
            title="Zichtbaar onderzoek",
            status="open",
        )
        db.session.add(gov)
        db.session.commit()
        resp = auth_client.get(f"/cms/workflow/case/{case_id}")
        assert resp.status_code == 200
        assert b"Zichtbaar onderzoek" in resp.data
        assert f"{case.case_number}-01".encode() in resp.data

    def test_standalone_read_route(self, app, auth_client):
        client_id, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        gov = Investigation(
            tenant_id=case.tenant_id,
            case_id=case_id,
            sequence_no=1,
            title="Leesbaar onderzoek",
            status="open",
        )
        db.session.add(gov)
        db.session.commit()
        resp = auth_client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert resp.status_code == 200
        assert b"Leesbaar onderzoek" in resp.data

    def test_archived_hidden_by_default_and_visible_with_flag(self, app, auth_client):
        client_id, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        a = Investigation(
            tenant_id=case.tenant_id, case_id=case_id, sequence_no=1,
            title="Actief onderzoek", status="open",
        )
        b = Investigation(
            tenant_id=case.tenant_id, case_id=case_id, sequence_no=2,
            title="Gearchiveerd onderzoek", status="open",
            archived_at=datetime.now(UTC),
        )
        db.session.add_all([a, b])
        db.session.commit()
        url = f"/cms/workflow/case/{case_id}/investigations"
        default = auth_client.get(url)
        assert b"Actief onderzoek" in default.data
        assert b"Gearchiveerd onderzoek" not in default.data
        with_archived = auth_client.get(url + "?show_archived=1")
        assert b"Gearchiveerd onderzoek" in with_archived.data

    def test_empty_case_shows_empty_state(self, app, auth_client):
        _, case_id = _make_client_and_case()
        resp = auth_client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert resp.status_code == 200
        assert b"No investigations yet." in resp.data


class TestCaseAndTenantIsolation:
    def test_investigations_are_per_case(self, app, auth_client):
        client_id, case_a = _make_client_and_case()
        _, case_b = _make_client_and_case()
        case = db.session.get(Case, case_a)
        gov = Investigation(
            tenant_id=case.tenant_id, case_id=case_a, sequence_no=1,
            title="Enkel in zaak A", status="open",
        )
        db.session.add(gov)
        db.session.commit()
        page_a = auth_client.get(f"/cms/workflow/case/{case_a}/investigations")
        page_b = auth_client.get(f"/cms/workflow/case/{case_b}/investigations")
        assert b"Enkel in zaak A" in page_a.data
        assert b"Enkel in zaak A" not in page_b.data
        _create_inv(auth_client, case_b, {"title": "Zaak B"})
        inv_b = Investigation.query.filter_by(case_id=case_b).one()
        assert inv_b.sequence_no == 1

    def test_cross_tenant_user_cannot_read(self, app, auth_client):
        from cms.models import Tenant

        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        tenant_b = Tenant(
            name="Andere Tenant",
            slug=f"x-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        other = _make_user("investigator", tenant_id=tenant_b.id)
        client = _login_as(app.test_client(), other)
        resp = client.get(f"/cms/workflow/case/{case.id}/investigations")
        assert resp.status_code == 403
        resp = _create_inv(client, case.id, {"title": "niet toestaan"})
        assert resp.status_code == 403
        assert Investigation.query.filter_by(case_id=case.id).count() == 0


class TestViewerAccess:
    def test_viewer_with_case_access_can_read_but_not_screen(self, app, auth_client):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        client_id, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.created_by = viewer.id
        db.session.commit()
        gov = Investigation(
            tenant_id=case.tenant_id, case_id=case_id, sequence_no=1,
            title="Viewer mag dit lezen", status="open",
        )
        db.session.add(gov)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        read = client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert read.status_code == 200
        assert b"Viewer mag dit lezen" in read.data
        assert b"+ add" not in read.data
        assert b"addInvestigationForm" not in read.data
        detail = client.get(f"/cms/workflow/case/{case_id}")
        assert detail.status_code == 403

    def test_viewer_cannot_create(self, app, auth_client):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.created_by = viewer.id
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = _create_inv(client, case_id, {"title": "Viewer poging"})
        assert resp.status_code == 403
        assert Investigation.query.filter_by(case_id=case_id).count() == 0

    def test_viewer_cannot_archive(self, app, auth_client):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        client_id, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.created_by = viewer.id
        db.session.commit()
        gov = Investigation(
            tenant_id=case.tenant_id, case_id=case_id, sequence_no=1,
            title="Niet archiveren", status="open",
        )
        db.session.add(gov)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.post(f"/cms/workflow/api/investigations/{gov.id}/archive")
        assert resp.status_code == 403
        db.session.refresh(gov)
        assert gov.archived_at is None

    def test_viewer_without_case_access_gets_nothing(self, app, auth_client):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.created_by = None
        case.lead_investigator_id = None
        case.assigned_to = None
        db.session.commit()
        gov = Investigation(
            tenant_id=case.tenant_id, case_id=case_id, sequence_no=1,
            title="Geheim onderzoek", status="open",
        )
        db.session.add(gov)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert resp.status_code == 403


class TestArchiveRestore:
    def _made_investigator(self):
        inv = _make_user("investigator", tenant_id=_admin_tenant_id())
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        case.investigators.append(inv)
        db.session.commit()
        return inv, case_id

    def test_archive_restore_cycle_and_handle_immutable_fields(self, app, auth_client):
        inv_user, case_id = self._made_investigator()
        client = _login_as(app.test_client(), inv_user)
        _create_inv(client, case_id, {"title": "Cyclus"})
        gov = Investigation.query.filter_by(case_id=case_id).one()
        before = (gov.human_number, gov.case_id, gov.tenant_id, gov.sequence_no)

        resp = client.post(f"/cms/workflow/api/investigations/{gov.id}/archive", json={})
        assert resp.status_code == 200
        db.session.refresh(gov)
        assert gov.archived_at is not None
        after_archive = (gov.human_number, gov.case_id, gov.tenant_id, gov.sequence_no)
        assert after_archive == before

        hidden = client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert b"Cyclus" not in hidden.data

        resp = client.post(f"/cms/workflow/api/investigations/{gov.id}/restore", json={})
        assert resp.status_code == 200
        db.session.refresh(gov)
        assert gov.archived_at is None
        assert (gov.human_number, gov.case_id, gov.tenant_id, gov.sequence_no) == before

        visible = client.get(f"/cms/workflow/case/{case_id}/investigations")
        assert b"Cyclus" in visible.data

    def test_archive_restore_from_html_form(self, app, auth_client):
        _, case_id = _make_client_and_case()
        _create_inv(auth_client, case_id, {"title": "Formulier"})
        gov = Investigation.query.filter_by(case_id=case_id).one()
        resp = auth_client.post(
            f"/cms/workflow/api/investigations/{gov.id}/archive",
            data={"csrf_token": "x"},
        )
        assert resp.status_code == 302
        db.session.refresh(gov)
        assert gov.archived_at is not None

    def test_unknown_investigation_404(self, app, auth_client):
        resp = auth_client.post(f"/cms/workflow/api/investigations/{uuid.uuid4()}/archive")
        assert resp.status_code == 404


class TestAuditLog:
    def test_audit_entries_for_create_archive_restore(self, app, auth_client):
        _, case_id = _make_client_and_case()
        _create_inv(auth_client, case_id, {"title": "Audit"})
        gov = Investigation.query.filter_by(case_id=case_id).one()
        auth_client.post(f"/cms/workflow/api/investigations/{gov.id}/archive")
        auth_client.post(f"/cms/workflow/api/investigations/{gov.id}/restore")

        created = AuditLog.query.filter_by(
            entity_type="investigation", action="create", entity_id=gov.id
        ).first()
        archived = AuditLog.query.filter_by(
            entity_type="investigation", action="archive", entity_id=gov.id
        ).first()
        restored = AuditLog.query.filter_by(
            entity_type="investigation", action="restore", entity_id=gov.id
        ).first()
        assert created is not None
        assert created.description and archived.description and restored.description
        assert archived.case_id == case_id
        assert restored is not None