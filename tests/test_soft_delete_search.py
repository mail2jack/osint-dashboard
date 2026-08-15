"""Soft-delete/archive consistency tests for search, dashboard, exports and views.

Covers P1 commit 2:
- FTS search excludes soft-deleted/archived subjects, cases and findings
- global search and super-admin global search exclude archived items
- exports, case views and the workflow dashboard exclude soft-deleted entities
"""

import uuid
from datetime import UTC, datetime

from cms.models import (
    Case,
    Client,
    Finding,
    Subject,
    db,
)


def _make_case(owner=None):
    client = Client(
        name="Test Client",
        contact_person="Test",
        contact_email="test@test.nl",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        client_id=client.id,
        title="Searchable Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=owner.id if owner else 1,
    )
    db.session.add(case)
    db.session.flush()
    return case


def _post_fts(client, query, scope="all"):
    return client.post(
        "/cms/api/search/fts", json={"query": query, "scope": scope, "limit": 50}
    )


class TestFTSSearchSoftDelete:
    def test_fts_excludes_deleted_subject(self, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="ZebraQux Gone", subject_type="person")
        subject.is_deleted = True
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="subjects")
        assert resp.status_code == 200
        names = [s["name"] for s in resp.get_json()["subjects"]]
        assert "ZebraQux Gone" not in names

    def test_fts_includes_active_subject(self, auth_client):
        subject = Subject(name="ZebraQux Active", subject_type="person")
        db.session.add(subject)
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="subjects")
        assert resp.status_code == 200
        names = [s["name"] for s in resp.get_json()["subjects"]]
        assert "ZebraQux Active" in names

    def test_fts_excludes_deleted_case(self, auth_client):
        case = _make_case(owner=None)
        case.title = "ZebraQux Deleted Case"
        case.is_deleted = True
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="cases")
        assert resp.status_code == 200
        titles = [c["title"] for c in resp.get_json()["cases"]]
        assert "ZebraQux Deleted Case" not in titles

    def test_fts_excludes_archived_case(self, auth_client):
        case = _make_case(owner=None)
        case.title = "ZebraQux Archived Case"
        case.archived_at = datetime.now(UTC)
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="cases")
        assert resp.status_code == 200
        titles = [c["title"] for c in resp.get_json()["cases"]]
        assert "ZebraQux Archived Case" not in titles

    def test_fts_excludes_deleted_finding(self, auth_client):
        case = _make_case(owner=None)
        finding = Finding(
            case_id=case.id,
            title="ZebraQux Deleted Finding",
            content="content",
            created_by=1,
        )
        finding.is_deleted = True
        db.session.add(finding)
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="findings")
        assert resp.status_code == 200
        titles = [f["title"] for f in resp.get_json()["findings"]]
        assert "ZebraQux Deleted Finding" not in titles

    def test_fts_excludes_archived_finding(self, auth_client):
        case = _make_case(owner=None)
        finding = Finding(
            case_id=case.id,
            title="ZebraQux Archived Finding",
            content="content",
            created_by=1,
        )
        finding.archived_at = datetime.now(UTC)
        db.session.add(finding)
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="findings")
        assert resp.status_code == 200
        titles = [f["title"] for f in resp.get_json()["findings"]]
        assert "ZebraQux Archived Finding" not in titles

    def test_fts_includes_active_finding(self, auth_client):
        case = _make_case(owner=None)
        finding = Finding(
            case_id=case.id,
            title="ZebraQux Active Finding",
            content="content",
            created_by=1,
        )
        db.session.add(finding)
        db.session.commit()

        resp = _post_fts(auth_client, "ZebraQux", scope="findings")
        assert resp.status_code == 200
        titles = [f["title"] for f in resp.get_json()["findings"]]
        assert "ZebraQux Active Finding" in titles


class TestGlobalSearchArchived:
    def test_search_excludes_archived_finding(self, auth_client):
        case = _make_case(owner=None)
        finding = Finding(
            case_id=case.id,
            title="ZebraQux Archived Global",
            content="content",
            created_by=1,
        )
        finding.archived_at = datetime.now(UTC)
        db.session.add(finding)
        db.session.commit()

        resp = auth_client.get("/cms/search?q=ZebraQux&type=findings")
        assert resp.status_code == 200
        assert "ZebraQux Archived Global" not in resp.get_data(as_text=True)

    def test_search_excludes_archived_case(self, auth_client):
        case = _make_case(owner=None)
        case.title = "ZebraQux Archived Case Global"
        case.archived_at = datetime.now(UTC)
        db.session.commit()

        resp = auth_client.get("/cms/search?q=ZebraQux&type=cases")
        assert resp.status_code == 200
        assert "ZebraQux Archived Case Global" not in resp.get_data(as_text=True)

    def test_admin_global_search_excludes_archived_finding(self, auth_client):
        case = _make_case(owner=None)
        finding = Finding(
            case_id=case.id,
            title="ZebraQux Archived Super",
            content="content",
            created_by=1,
        )
        finding.archived_at = datetime.now(UTC)
        db.session.add(finding)
        db.session.commit()

        resp = auth_client.get("/cms/admin/global-search?q=ZebraQux&type=findings")
        assert resp.status_code == 200
        assert "ZebraQux Archived Super" not in resp.get_data(as_text=True)


class TestExportsAndViewsSoftDelete:
    def test_export_case_json_excludes_deleted_subject(self, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="Exported Gone", subject_type="person")
        subject.is_deleted = True
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.commit()

        resp = auth_client.get(f"/cms/cases/{case.id}/export-json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert all(s["name"] != "Exported Gone" for s in data["subjects"])

    def test_view_case_excludes_deleted_subject(self, auth_client):
        case = _make_case(owner=None)
        subject = Subject(name="View Gone Subject", subject_type="person")
        subject.is_deleted = True
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.commit()

        resp = auth_client.get(f"/cms/cases/{case.id}")
        assert resp.status_code == 200
        assert "View Gone Subject" not in resp.get_data(as_text=True)

    def test_workflow_dashboard_excludes_deleted_case(self, auth_client):
        case = _make_case(owner=None)
        case.title = "Dashboard Deleted Case"
        case.is_deleted = True
        db.session.commit()

        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200
        assert "Dashboard Deleted Case" not in resp.get_data(as_text=True)
