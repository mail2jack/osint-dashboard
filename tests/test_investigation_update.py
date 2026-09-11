"""
Investigation edit/update — PR2.

Covers the canonical case-scoped update endpoint
``/api/case/<case_id>/investigations/<investigation_id>/update`` (via the
edit UI / JSON) plus the edit GET page:

    * admin / investigator may update and see the edit form;
    * junior / viewer get 403 (no write rights);
    * wrong-case and other-tenant requests get 403/404 with no mutation;
    * archived investigations reject updates with 409 (no mutation, no audit);
    * unknown fields, empty title and over-long values return 400;
    * audit + update share one transaction: an audit failure rolls back the
      update and returns 500;

Run on SQLite (default ``tests/`` suite). PostgreSQL/RLS isolation lives in
``tests/test_postgres_investigation_update_rls.py``.
"""

import uuid
from datetime import UTC, datetime

import pytest

from cms.models import AuditLog, Case, Client, FeatureFlag, Investigation, User, db

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
        username=username or f"upd_{token}",
        email=f"upd_{token}@localhost",
        full_name="Update Test User",
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
    client = Client(name="Inv Update Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title="Inv Update Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.commit()
    return client.id, case.id


def _make_investigation(case, title="Update onderzoek", **kwargs):
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


def _update_url(case_id, investigation_id):
    return f"/cms/workflow/api/case/{case_id}/investigations/{investigation_id}/update"


def _edit_url(case_id, investigation_id):
    return f"/cms/workflow/case/{case_id}/investigations/{investigation_id}/edit"


def _update_audit(investigation_id):
    return AuditLog.query.filter_by(
        entity_type="investigation", entity_id=investigation_id, action="update"
    ).all()


def _enabled_case_and_investigation():
    _enable_workspace(_admin_tenant_id(), enabled=True)
    _, case_id = _make_client_and_case()
    case = db.session.get(Case, case_id)
    inv = _make_investigation(
        case, title="Originele titel", instructions="Inst", notes=None
    )
    return case, inv


class TestFlagGate:
    def test_update_flag_off_returns_404(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.post(_update_url(case.id, inv.id), json={"title": "x"})
        assert resp.status_code == 404

    def test_edit_flag_off_returns_404(self, app, auth_client):
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)
        resp = auth_client.get(_edit_url(case.id, inv.id))
        assert resp.status_code == 404


class TestUpdateBasics:
    def test_update_title_and_fields(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id),
            json={
                "title": "Nieuwe titel",
                "instructions": "Nieuwe instructies",
                "notes": "Nieuwe notities",
            },
        )
        assert resp.status_code == 200
        db.session.refresh(inv)
        assert inv.title == "Nieuwe titel"
        assert inv.instructions == "Nieuwe instructies"
        assert inv.notes == "Nieuwe notities"

        audits = _update_audit(inv.id)
        assert len(audits) == 1
        entry = audits[0]
        assert entry.old_values == {
            "title": "Originele titel",
            "instructions": "Inst",
            "notes": None,
        }
        assert entry.new_values == {
            "title": "Nieuwe titel",
            "instructions": "Nieuwe instructies",
            "notes": "Nieuwe notities",
        }

    def test_partial_update_only_title(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "Alleen titel"}
        )
        assert resp.status_code == 200
        db.session.refresh(inv)
        assert inv.title == "Alleen titel"
        assert inv.instructions == "Inst"

        entry = _update_audit(inv.id)[0]
        assert entry.new_values == {
            "title": "Alleen titel",
            "instructions": "Inst",
            "notes": None,
        }

    def test_clear_notes_with_empty_string(self, app, auth_client):
        case = db.session.get(Case, _enabled_case_and_investigation()[1].case_id)
        inv = _make_investigation(
            case, title="Notitiewissen", instructions="vn", notes="weg",
            sequence_no=2,
        )
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"notes": ""}
        )
        assert resp.status_code == 200
        db.session.refresh(inv)
        assert inv.notes is None
        assert inv.instructions == "vn"

    def test_noop_update_writes_no_extra_audit(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        before = len(_update_audit(inv.id))
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "Originele titel"}
        )
        assert resp.status_code == 200
        assert len(_update_audit(inv.id)) == before

    def test_create_archive_restore_updates_do_not_leak_into_update_audit(
        self, app, auth_client
    ):
        case, inv = _enabled_case_and_investigation()
        auth_client.post(f"/cms/workflow/api/investigations/{inv.id}/archive", json={})
        auth_client.post(f"/cms/workflow/api/investigations/{inv.id}/restore", json={})
        assert len(_update_audit(inv.id)) == 0


class TestUpdateValidation:
    def test_empty_title_returns_400(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        for bad in ("", "   "):
            resp = auth_client.post(
                _update_url(case.id, inv.id), json={"title": bad}
            )
            assert resp.status_code == 400
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []

    def test_missing_payload_returns_400(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(_update_url(case.id, inv.id), json={})
        assert resp.status_code == 400
        assert _update_audit(inv.id) == []

    def test_unknown_field_returns_400(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id),
            json={"title": "x", "sequence_no": 99, "archived_at": None},
        )
        assert resp.status_code == 400
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []

    def test_overlong_title_returns_400(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "x" * 301}
        )
        assert resp.status_code == 400
        db.session.refresh(inv)
        assert inv.title == "Originele titel"

    def test_overlong_notes_returns_400(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"notes": "x" * 5001}
        )
        assert resp.status_code == 400
        assert _update_audit(inv.id) == []

    def test_boundary_title_length_300_accepted(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "x" * 300}
        )
        assert resp.status_code == 200

    def test_payload_must_be_json_object(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        for bad in (["title"], ["title", "x"], 42, "title"):
            resp = auth_client.post(
                _update_url(case.id, inv.id), json=bad
            )
            assert resp.status_code == 400
        resp = auth_client.post(
            _update_url(case.id, inv.id), data="null", content_type="application/json"
        )
        assert resp.status_code == 400
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []

    @pytest.mark.parametrize(
        "payload",
        [
            {"title": 123},
            {"title": True},
            {"notes": []},
            {"notes": {"x": "y"}},
            {"instructions": ["a", "b"]},
            {"title": None},
        ],
    )
    def test_non_string_field_values_return_400(self, app, auth_client, payload):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(_update_url(case.id, inv.id), json=payload)
        assert resp.status_code == 400
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []

    def test_explicit_null_clears_optional_field(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"notes": None}
        )
        assert resp.status_code == 200
        db.session.refresh(inv)
        assert inv.notes is None
        assert inv.title == "Originele titel"


class TestUpdateArchived:
    def test_archived_investigation_update_409_no_mutation_no_audit(
        self, app, auth_client
    ):
        case, inv = _enabled_case_and_investigation()
        archived = auth_client.post(
            f"/cms/workflow/api/investigations/{inv.id}/archive", json={}
        )
        assert archived.status_code == 200

        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "Nooit toepassen"}
        )
        assert resp.status_code == 409
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []

    def test_edit_page_archived_redirects_to_detail(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        auth_client.post(f"/cms/workflow/api/investigations/{inv.id}/archive", json={})
        resp = auth_client.get(_edit_url(case.id, inv.id))
        assert resp.status_code == 302


class TestStatusInvariant:
    """require_open/require_archived enforce a POSITIVE invariant.

    Only ``open + archived_at is None`` (resp. ``archived + archived_at set``)
    pass; unknown or inconsistent status/timestamp combinations are 409 with no
    mutation or audit.
    """

    def test_archive_closed_status_409(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        inv.status = "closed"
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/investigations/{inv.id}/archive", json={}
        )
        assert resp.status_code == 409
        db.session.refresh(inv)
        assert inv.status == "closed"
        assert inv.archived_at is None
        assert AuditLog.query.filter_by(
            entity_type="investigation", entity_id=inv.id, action="archive"
        ).count() == 0

    def test_archive_inconsistent_open_with_timestamp_409(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        inv.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/investigations/{inv.id}/archive", json={}
        )
        assert resp.status_code == 409
        db.session.refresh(inv)
        assert AuditLog.query.filter_by(
            entity_type="investigation", entity_id=inv.id, action="archive"
        ).count() == 0

    @pytest.mark.parametrize(
        ("status", "archived_at"),
        [
            ("closed", None),
            ("archived", None),
            ("open", "SET"),  # inconsistent: open status with a timestamp
        ],
    )
    def test_update_rejects_non_open_combinations(
        self, app, auth_client, status, archived_at
    ):
        case, inv = _enabled_case_and_investigation()
        inv.status = status
        inv.archived_at = datetime.now(UTC) if archived_at == "SET" else None
        db.session.commit()
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "Nooit"}
        )
        assert resp.status_code == 409
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []

    @pytest.mark.parametrize(
        ("status", "archived_at", "should_200"),
        [
            ("archived", None, False),
            ("closed", "SET", False),
            ("open", "SET", False),
        ],
    )
    def test_restore_requires_archived_positive_invariant(
        self, app, auth_client, status, archived_at, should_200
    ):
        case, inv = _enabled_case_and_investigation()
        inv.status = status
        inv.archived_at = datetime.now(UTC) if archived_at == "SET" else None
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/investigations/{inv.id}/restore", json={}
        )
        assert resp.status_code == (200 if should_200 else 409)
        assert AuditLog.query.filter_by(
            entity_type="investigation", entity_id=inv.id, action="restore"
        ).count() == (1 if should_200 else 0)


class TestUpdateAccess:
    def test_investigator_can_update(self, app):
        client = app.test_client()
        _enable_workspace(_admin_tenant_id(), enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv_user = _make_user("investigator", tenant_id=case.tenant_id)
        case.investigators.append(inv_user)
        db.session.commit()
        inv = _make_investigation(case)

        _login_as(client, inv_user)
        resp = client.post(
            _update_url(case.id, inv.id), json={"title": "Door investigator"}
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("role", ["junior_investigator", "viewer"])
    def test_junior_and_viewer_cannot_update(self, app, role):
        client = app.test_client()
        _enable_workspace(_admin_tenant_id(), enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        nobody = _make_user(role, tenant_id=case.tenant_id)
        case.investigators.append(nobody)
        db.session.commit()
        inv = _make_investigation(case)

        _login_as(client, nobody)
        resp = client.post(
            _update_url(case.id, inv.id), json={"title": "Geweigerd"}
        )
        assert resp.status_code == 403
        db.session.refresh(inv)
        assert inv.title == "Update onderzoek"

    def test_wrong_case_pair_returns_404(self, app, auth_client):
        _enable_workspace(_admin_tenant_id(), enabled=True)
        _, case_a = _make_client_and_case()
        _, case_b = _make_client_and_case()
        inv = _make_investigation(db.session.get(Case, case_a))
        resp = auth_client.post(
            _update_url(case_b, inv.id), json={"title": "Verkeerd geval"}
        )
        assert resp.status_code == 404
        db.session.refresh(inv)
        assert inv.title == "Update onderzoek"

    def test_other_tenant_update_rejected(self, app):
        from cms.models import Tenant

        client = app.test_client()
        admin = User.query.filter_by(username="admin").first()
        _enable_workspace(admin.tenant_id, enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        inv = _make_investigation(case)

        tenant_b = Tenant(
            name="Update B",
            slug=f"x-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(tenant_b)
        db.session.flush()
        other = _make_user("investigator", tenant_id=tenant_b.id)
        _login_as(client, other)
        resp = client.post(
            _update_url(case.id, inv.id), json={"title": "Ander tenant"}
        )
        assert resp.status_code in (403, 404)
        db.session.refresh(inv)
        assert inv.title == "Update onderzoek"

    def test_nonexistent_investigation_404(self, app, auth_client):
        _enable_workspace(_admin_tenant_id(), enabled=True)
        _, case_id = _make_client_and_case()
        resp = auth_client.post(
            _update_url(case_id, str(uuid.uuid4())), json={"title": "x"}
        )
        assert resp.status_code == 404


class TestUpdateRollback:
    def test_audit_failure_rolls_back_update(self, app, auth_client, monkeypatch):
        case, inv = _enabled_case_and_investigation()

        def _boom(**kwargs):
            raise RuntimeError("audit write boom")

        monkeypatch.setattr(AuditLog, "log", _boom)
        resp = auth_client.post(
            _update_url(case.id, inv.id), json={"title": "Zal rollbacken"}
        )
        assert resp.status_code == 500
        db.session.refresh(inv)
        assert inv.title == "Originele titel"
        assert _update_audit(inv.id) == []


class TestEditPage:
    def test_edit_page_renders_values(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.get(_edit_url(case.id, inv.id))
        assert resp.status_code == 200
        assert b"Originele titel" in resp.data
        assert b"Inst" in resp.data

    def test_edit_page_junior_forbidden(self, app):
        client = app.test_client()
        _enable_workspace(_admin_tenant_id(), enabled=True)
        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        junior = _make_user("junior_investigator", tenant_id=case.tenant_id)
        case.investigators.append(junior)
        db.session.commit()
        inv = _make_investigation(case)
        _login_as(client, junior)
        resp = client.get(_edit_url(case.id, inv.id))
        assert resp.status_code == 403


class TestFormFlow:
    def test_form_post_redirects_and_updates(self, app, auth_client):
        case, inv = _enabled_case_and_investigation()
        resp = auth_client.post(
            _update_url(case.id, inv.id),
            data={"csrf_token": "x", "title": "Via formulier", "notes": ""},
        )
        assert resp.status_code == 302
        db.session.refresh(inv)
        assert inv.title == "Via formulier"
        assert inv.notes is None
        assert len(_update_audit(inv.id)) == 1