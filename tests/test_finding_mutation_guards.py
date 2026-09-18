"""Finding-mutation guard + screenshot hardening + audit semantics (P2-1/P2-5).

Central guard (``_resolve_mutable_finding`` / ``_resolve_findings_for_archive_restore``):
  - deleted → 404 (all mutation endpoints)
  - archived → 409 (verify/comment/report-flag/screenshot/single-delete/batch-delete)
  - archive only on active; restore only on archived; double archive/restore → 409
  - failed mutations write NO audit (success-only audit)

Screenshot hardening (P2-5):
  - oversized file → 413 + JSON error + no row + no file
  - invalid magic bytes → 400
  - count cap (MAX_SCREENSHOTS_PER_FINDING) → 409
  - DB failure → rollback + orphaned file removed

Viewer + junior_investigator → 403 (role gate) and can_write=False.
"""

import io
import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.orm.exc import ObjectDeletedError

from cms.models import (
    AuditLog,
    Case,
    Client,
    Finding,
    FindingScreenshot,
    FindingCaptureJob,
    Subject,
    Tenant,
    User,
    db,
)
from cms.workflow.routes import (
    MAX_SCREENSHOT_FILE_BYTES,
    MAX_SCREENSHOTS_PER_FINDING,
)


# ---------------------------------------------------------------------------
# Helpers (duplicated from test_investigation_workspace per codebase convention)
# ---------------------------------------------------------------------------

def _admin_tenant_id():
    return db.session.execute(
        db.text("SELECT tenant_id FROM users WHERE username='admin'")
    ).scalar()


def _make_user(role, tenant_id=None, username=None):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=username or f"mg_{token}",
        email=f"mg_{token}@localhost",
        full_name="Guard Test User",
        role=role,
        is_active=True,
    )
    if tenant_id:
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
    return client


class TestFindingCaptureRequest:
    """The web process queues captures only; it never starts a browser."""

    def _objects(self):
        tenant_id = _admin_tenant_id()
        user = _make_user("investigator", tenant_id=tenant_id)
        case = _make_case(tenant_id=tenant_id, creator=user)
        subject = _make_subject(case)
        finding = _make_finding(case, subject)
        db.session.commit()
        return user, case, finding

    def _post(self, client, case, finding, body):
        return client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{finding.id}/capture-requests",
            json=body,
        )

    def test_flag_off_hides_request_endpoint(self, app, client, db_session):
        user, case, finding = self._objects()
        _login_as(client, user)
        response = self._post(client, case, finding, {"confirm": True, "target_url": "https://example.test"})
        assert response.status_code == 404
        assert FindingCaptureJob.query.count() == 0

    def test_confirmed_request_is_queued_and_audited(self, app, client, db_session):
        user, case, finding = self._objects()
        _login_as(client, user)
        with patch("cms.workflow.routes.check_feature", return_value=True), patch(
            "cms.services.finding_capture_queue.validate_capture_url", return_value=(True, "")
        ):
            response = self._post(client, case, finding, {"confirm": True, "target_url": " https://example.test/a "})
        assert response.status_code == 202
        job = FindingCaptureJob.query.one()
        assert job.target_url == "https://example.test/a"
        assert job.status == "queued"
        assert job.request_metadata == {"requested_via": "finding_capture_request"}
        audit = AuditLog.query.filter_by(entity_type="finding_capture_job", entity_id=job.id).one()
        assert audit.action == "create"

    @pytest.mark.parametrize(
        "body", [{}, {"confirm": False, "target_url": "https://example.test"}, {"confirm": True}, {"confirm": True, "target_url": 1}, []]
    )
    def test_request_requires_confirmed_json_url(self, app, client, db_session, body):
        user, case, finding = self._objects()
        _login_as(client, user)
        with patch("cms.workflow.routes.check_feature", return_value=True):
            response = self._post(client, case, finding, body)
        assert response.status_code == 400
        assert FindingCaptureJob.query.count() == 0
        assert AuditLog.query.filter_by(entity_type="finding_capture_job").count() == 0

    def test_second_active_request_is_conflict_without_extra_audit(self, app, client, db_session):
        user, case, finding = self._objects()
        _login_as(client, user)
        with patch("cms.workflow.routes.check_feature", return_value=True), patch(
            "cms.services.finding_capture_queue.validate_capture_url", return_value=(True, "")
        ):
            assert self._post(client, case, finding, {"confirm": True, "target_url": "https://example.test/a"}).status_code == 202
            response = self._post(client, case, finding, {"confirm": True, "target_url": "https://example.test/b"})
        assert response.status_code == 409
        assert FindingCaptureJob.query.count() == 1
        assert AuditLog.query.filter_by(entity_type="finding_capture_job").count() == 1

    def test_wrong_case_never_creates_a_capture_request(self, app, client, db_session):
        user, case, finding = self._objects()
        other_case = _make_case(tenant_id=case.tenant_id, creator=user)
        db.session.commit()
        _login_as(client, user)
        with patch("cms.workflow.routes.check_feature", return_value=True):
            response = self._post(
                client,
                other_case,
                finding,
                {"confirm": True, "target_url": "https://example.test"},
            )
        assert response.status_code == 404
        assert FindingCaptureJob.query.count() == 0
        assert AuditLog.query.filter_by(entity_type="finding_capture_job").count() == 0


def _make_case(tenant_id=None, title="Guard Case", creator=None):
    tenant_id = tenant_id or _admin_tenant_id()
    client = Client(name="Guard Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title=title,
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date() if hasattr(__import__('datetime', fromlist=['UTC']), 'UTC') else None,
    )
    db.session.add(case)
    db.session.flush()
    if creator:
        case.created_by = creator.id
        db.session.commit()
    return case


def _make_subject(case, name="Guard Subject"):
    subj = Subject(
        tenant_id=case.tenant_id,
        name=name,
        subject_type="person",
        email="guard@example.com",
    )
    subj.encrypt_identifiers()
    db.session.add(subj)
    db.session.flush()
    return subj


def _make_finding(case, subject, **kwargs):
    finding = Finding(
        tenant_id=case.tenant_id,
        case_id=case.id,
        subject_id=subject.id,
        title=kwargs.pop("title", "Guard Finding"),
        content=kwargs.pop("content", "guard evidence"),
        source_type=kwargs.pop("source_type", "manual"),
        status=kwargs.pop("status", "candidate"),
        verified=kwargs.pop("verified", False),
        created_by=kwargs.pop("created_by", None)
        or User.query.filter_by(username="admin").first().id,
        archived_at=kwargs.pop("archived_at", None),
        comment=kwargs.pop("comment", None),
        include_in_report=kwargs.pop("include_in_report", True),
    )
    db.session.add(finding)
    db.session.flush()
    return finding


from datetime import UTC, datetime


def _scaffold(user=None):
    """Return (case, subject, finding) for API tests with admin access."""
    tid = _admin_tenant_id()
    admin = User.query.filter_by(username="admin").first()
    case = _make_case(tenant_id=tid)
    if user:
        case.created_by = user.id
    subject = _make_subject(case)
    finding = _make_finding(
        case, subject, created_by=(user.id if user else admin.id)
    )
    db.session.commit()
    return case, subject, finding


def _findings_json(case_id, finding_id):
    return f"/cms/workflow/api/case/{case_id}/findings/{finding_id}"


def _archive_json(finding_id):
    return f"/cms/workflow/api/findings/{finding_id}/archive"


def _restore_json(finding_id):
    return f"/cms/workflow/api/findings/{finding_id}/restore"


# ---------------------------------------------------------------------------
# Guard: deleted → 404
# ---------------------------------------------------------------------------


class TestDeletedFindingReturns404:
    def test_verify_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 404

    def test_comment_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/comment",
            json={"comment": "should fail"},
        )
        assert resp.status_code == 404

    def test_report_flag_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/report-flag",
            json={"include_in_report": False},
        )
        assert resp.status_code == 404

    def test_screenshot_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/screenshots",
            json={"url": "", "source_url": "", "notes": ""},
        )
        assert resp.status_code == 404

    def test_delete_single_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/delete",
        )
        assert resp.status_code == 404

    def test_archive_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(_archive_json(finding.id))
        assert resp.status_code == 404

    def test_restore_deleted_404(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(_restore_json(finding.id))
        assert resp.status_code == 404

    def test_batch_delete_skips_deleted(self, auth_client):
        case, _, finding = _scaffold()
        finding.soft_delete()
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/batch-delete",
            json={"ids": [finding.id]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == 0


# ---------------------------------------------------------------------------
# Guard: archived → 409
# ---------------------------------------------------------------------------


class TestArchivedFindingReturns409:
    def test_verify_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "Finding is archived"

    def test_comment_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/comment",
            json={"comment": "should fail"},
        )
        assert resp.status_code == 409

    def test_report_flag_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/report-flag",
            json={"include_in_report": False},
        )
        assert resp.status_code == 409

    def test_screenshot_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/screenshots",
            json={"url": "", "source_url": "https://x", "notes": ""},
        )
        assert resp.status_code == 409

    def test_delete_single_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(_findings_json(case.id, finding.id) + "/delete")
        assert resp.status_code == 409

    def test_batch_delete_archived_409_nothing_deleted(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        count_before = Finding.query.filter_by(case_id=case.id, is_deleted=False).count()
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/batch-delete",
            json={"ids": [finding.id]},
        )
        assert resp.status_code == 409
        assert Finding.query.filter_by(case_id=case.id, is_deleted=False).count() == count_before


# ---------------------------------------------------------------------------
# Archive / restore invariants
# ---------------------------------------------------------------------------


class TestArchiveRestoreInvariants:
    def test_archive_active_ok(self, auth_client):
        case, _, finding = _scaffold()
        resp = auth_client.post(_archive_json(finding.id), json={})
        assert resp.status_code == 200
        assert db.session.get(Finding, finding.id).archived_at is not None

    def test_archive_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(_archive_json(finding.id))
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "Finding is already archived"

    def test_restore_archived_ok(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        resp = auth_client.post(_restore_json(finding.id), json={})
        assert resp.status_code == 200
        assert db.session.get(Finding, finding.id).archived_at is None

    def test_restore_not_archived_409(self, auth_client):
        case, _, finding = _scaffold()
        resp = auth_client.post(_restore_json(finding.id))
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "Finding is not archived"

    def test_archive_delete_roundtrip(self, auth_client):
        case, _, finding = _scaffold()
        auth_client.post(_archive_json(finding.id), json={})
        auth_client.post(_restore_json(finding.id), json={})
        assert db.session.get(Finding, finding.id).archived_at is None

    def test_double_archive_409_only_one_audit(self, auth_client):
        case, _, finding = _scaffold()
        auth_client.post(_archive_json(finding.id))
        audit_before = AuditLog.query.filter_by(
            entity_type="finding", action="archive", entity_id=finding.id
        ).count()
        resp = auth_client.post(_archive_json(finding.id))
        assert resp.status_code == 409
        assert AuditLog.query.filter_by(
            entity_type="finding", action="archive", entity_id=finding.id
        ).count() == audit_before

    def test_double_restore_409(self, auth_client):
        case, _, finding = _scaffold()
        auth_client.post(_archive_json(finding.id))
        auth_client.post(_restore_json(finding.id))
        audit_count = AuditLog.query.filter_by(
            entity_type="finding", action="restore", entity_id=finding.id
        ).count()
        resp = auth_client.post(_restore_json(finding.id))
        assert resp.status_code == 409
        assert AuditLog.query.filter_by(
            entity_type="finding", action="restore", entity_id=finding.id
        ).count() == audit_count


class TestFindingArchiveRestoreNonJson:
    def test_legacy_post_commit_finding_read_is_caught(self, auth_client):
        """The poison listener really catches the pre-fix behavior."""
        _, _, finding = _scaffold()
        committed = False
        refresh_attempts = []

        def mark_committed(session):
            nonlocal committed
            committed = True

        def reject_expired_refresh(
            conn, cursor, statement, parameters, context, executemany
        ):
            normalized = " ".join(statement.upper().split())
            if committed and "FROM FINDINGS" in normalized:
                refresh_attempts.append(statement)
                raise ObjectDeletedError(inspect(finding))

        event.listen(db.session, "after_commit", mark_committed)
        event.listen(db.engine, "before_cursor_execute", reject_expired_refresh)
        try:
            finding.archived_at = datetime.now(UTC)
            db.session.commit()
            with pytest.raises(ObjectDeletedError):
                _ = finding.case_id
        finally:
            event.remove(db.session, "after_commit", mark_committed)
            event.remove(db.engine, "before_cursor_execute", reject_expired_refresh)

        assert refresh_attempts

    @pytest.mark.parametrize(
        ("endpoint", "initially_archived"),
        [("archive", False), ("restore", True)],
    )
    @pytest.mark.parametrize("referer", [None, "/cms/workflow/case/example"])
    def test_non_json_redirect_never_refreshes_finding_after_commit(
        self, auth_client, endpoint, initially_archived, referer
    ):
        """The old post-commit ORM read is a deliberately poisoned path.

        With the old ``finding.case_id`` redirect expression this listener is
        reached after commit and raises ``ObjectDeletedError``. The fixed
        scalar redirect must leave the listener untouched for both endpoints,
        with and without a Referer header.
        """
        case, _, finding = _scaffold()
        if initially_archived:
            finding.archived_at = datetime.now(UTC)
            db.session.commit()

        headers = {"Referer": referer} if referer else {}
        committed = False
        refresh_attempts = []

        def mark_committed(session):
            nonlocal committed
            committed = True

        def reject_expired_refresh(
            conn, cursor, statement, parameters, context, executemany
        ):
            normalized = " ".join(statement.upper().split())
            if committed and "FROM FINDINGS" in normalized:
                refresh_attempts.append(statement)
                raise ObjectDeletedError(inspect(finding))

        event.listen(db.session, "after_commit", mark_committed)
        event.listen(db.engine, "before_cursor_execute", reject_expired_refresh)
        try:
            response = auth_client.post(
                f"/cms/workflow/api/findings/{finding.id}/{endpoint}",
                headers=headers,
            )
        finally:
            event.remove(db.session, "after_commit", mark_committed)
            event.remove(db.engine, "before_cursor_execute", reject_expired_refresh)

        assert response.status_code == 302
        assert not refresh_attempts
        if referer:
            assert response.headers["Location"] == referer
        else:
            assert f"/cms/workflow/case/{case.id}" in response.headers["Location"]


# ---------------------------------------------------------------------------
# Audit: blocked mutations write no success audit
# ---------------------------------------------------------------------------


class TestBlockedMutationsWriteNoAudit:
    def test_archived_verify_no_audit(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        before = AuditLog.query.filter_by(
            entity_type="finding", action="verify", entity_id=finding.id
        ).count()
        auth_client.post(
            _findings_json(case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert AuditLog.query.filter_by(
            entity_type="finding", action="verify", entity_id=finding.id
        ).count() == before

    def test_archived_screenshot_no_audit(self, auth_client):
        case, _, finding = _scaffold()
        finding.archived_at = datetime.now(UTC)
        db.session.commit()
        before = AuditLog.query.filter_by(
            entity_type="finding_screenshot"
        ).filter(
            AuditLog.entity_id.in_(
                [s.id for s in FindingScreenshot.query.filter_by(finding_id=finding.id)]
            )
        ).count()
        auth_client.post(
            _findings_json(case.id, finding.id) + "/screenshots",
            json={"url": "", "source_url": "x", "notes": ""},
        )
        assert AuditLog.query.filter_by(
            entity_type="finding_screenshot"
        ).count() == before


# ---------------------------------------------------------------------------
# Screenshot upload hardening (P2-5)
# ---------------------------------------------------------------------------


class TestScreenshotUploadHardening:
    def test_valid_upload_ok(self, auth_client, tmp_path):
        case, _, finding = _scaffold()
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        with patch("cms.workflow.routes.SCREENSHOT_DIR", str(tmp_path)):
            resp = auth_client.post(
                _findings_json(case.id, finding.id) + "/screenshots",
                data={
                    "file": (io.BytesIO(png_header), "tiny.png"),
                    "source_url": "https://example.com",
                    "notes": "OK",
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        ss = FindingScreenshot.query.filter_by(finding_id=finding.id).one()
        assert ss.file_path
        assert os.path.exists(ss.file_path)

    def test_invalid_magic_bytes_400(self, auth_client, tmp_path):
        case, _, finding = _scaffold()
        with patch("cms.workflow.routes.SCREENSHOT_DIR", str(tmp_path)):
            resp = auth_client.post(
                _findings_json(case.id, finding.id) + "/screenshots",
                data={
                    "file": (io.BytesIO(b"not-an-image-data"), "fake.png"),
                    "source_url": "",
                    "notes": "",
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 400
        assert FindingScreenshot.query.filter_by(finding_id=finding.id).count() == 0

    def test_oversized_file_413(self, auth_client, tmp_path):
        case, _, finding = _scaffold()
        payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_SCREENSHOT_FILE_BYTES + 1)
        with patch("cms.workflow.routes.SCREENSHOT_DIR", str(tmp_path)):
            resp = auth_client.post(
                _findings_json(case.id, finding.id) + "/screenshots",
                data={
                    "file": (io.BytesIO(payload), "huge.png"),
                    "source_url": "",
                    "notes": "",
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 413
        assert "too large" in resp.get_json()["error"].lower()
        assert FindingScreenshot.query.filter_by(finding_id=finding.id).count() == 0
        assert len(os.listdir(str(tmp_path))) == 0

    def test_count_cap_409(self, auth_client):
        case, _, finding = _scaffold()
        for _ in range(MAX_SCREENSHOTS_PER_FINDING):
            db.session.add(
                FindingScreenshot(
                    id=str(uuid.uuid4()),
                    finding_id=finding.id,
                    tenant_id=case.tenant_id,
                    url="",
                )
            )
        db.session.commit()
        resp = auth_client.post(
            _findings_json(case.id, finding.id) + "/screenshots",
            json={"url": "", "source_url": "x", "notes": ""},
        )
        assert resp.status_code == 409
        assert "limit" in resp.get_json()["error"].lower()
        assert (
            FindingScreenshot.query.filter_by(finding_id=finding.id).count()
            == MAX_SCREENSHOTS_PER_FINDING
        )

    def test_db_failure_removes_file(self, auth_client, tmp_path):
        case, _, finding = _scaffold()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        with patch("cms.workflow.routes.SCREENSHOT_DIR", str(tmp_path)):
            with patch(
                "cms.workflow.routes.db.session.commit",
                side_effect=RuntimeError("DB down"),
            ):
                resp = auth_client.post(
                    _findings_json(case.id, finding.id) + "/screenshots",
                    data={
                        "file": (io.BytesIO(png), "fail.png"),
                        "source_url": "",
                        "notes": "",
                    },
                    content_type="multipart/form-data",
                )
        assert resp.status_code == 500
        assert resp.get_json()["error"] == "Internal error"
        assert FindingScreenshot.query.filter_by(finding_id=finding.id).count() == 0
        leftover_files = sorted(
            str(p.relative_to(tmp_path))
            for p in tmp_path.rglob("*")
            if p.is_file()
        )
        assert leftover_files == []

    def test_save_failure_removes_partial_file(self, auth_client, tmp_path):
        case, _, finding = _scaffold()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

        def _partial_save(self2, dest, buffer_size=16384):
            with open(dest, "wb") as fh:
                fh.write(png[:8])
            raise OSError("disk full")

        from werkzeug.datastructures import FileStorage

        with patch("cms.workflow.routes.SCREENSHOT_DIR", str(tmp_path)):
            with patch.object(FileStorage, "save", _partial_save):
                resp = auth_client.post(
                    _findings_json(case.id, finding.id) + "/screenshots",
                    data={
                        "file": (io.BytesIO(png), "fail.png"),
                        "source_url": "",
                        "notes": "",
                    },
                    content_type="multipart/form-data",
                )
        assert resp.status_code == 500
        assert resp.get_json()["error"] == "Internal error"
        assert str(tmp_path) not in resp.get_data(as_text=True)
        assert FindingScreenshot.query.filter_by(finding_id=finding.id).count() == 0
        assert AuditLog.query.filter_by(entity_type="finding_screenshot").count() == 0
        leftover_files = sorted(
            str(p.relative_to(tmp_path))
            for p in tmp_path.rglob("*")
            if p.is_file()
        )
        assert leftover_files == []


# ---------------------------------------------------------------------------
# Viewer + junior_investigator → 403 (role gate)
# ---------------------------------------------------------------------------


class TestRoleGateMutations:
    def test_viewer_verify_403(self, app):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.post(
            _findings_json(case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 403

    def test_viewer_comment_403(self, app):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.post(
            _findings_json(case.id, finding.id) + "/comment",
            json={"comment": "no"},
        )
        assert resp.status_code == 403

    def test_viewer_report_flag_403(self, app):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.post(
            _findings_json(case.id, finding.id) + "/report-flag",
            json={"include_in_report": False},
        )
        assert resp.status_code == 403

    def test_viewer_screenshot_403(self, app):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.post(
            _findings_json(case.id, finding.id) + "/screenshots",
            json={"url": "", "source_url": "x", "notes": ""},
        )
        assert resp.status_code == 403

    def test_viewer_archive_403(self, app):
        tid = _admin_tenant_id()
        viewer = _make_user("viewer", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), viewer)
        resp = client.post(_archive_json(finding.id))
        assert resp.status_code == 403

    def test_junior_verify_403(self, app):
        tid = _admin_tenant_id()
        junior = _make_user("junior_investigator", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), junior)
        resp = client.post(
            _findings_json(case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 403

    def test_junior_archive_403(self, app):
        tid = _admin_tenant_id()
        junior = _make_user("junior_investigator", tenant_id=tid)
        case = _make_case(tenant_id=tid)
        subj = _make_subject(case)
        finding = _make_finding(case, subj)
        db.session.commit()
        client = _login_as(app.test_client(), junior)
        resp = client.post(_archive_json(finding.id))
        assert resp.status_code == 403

    def test_cross_tenant_verify_403(self, app):
        case, _, finding = _scaffold()
        other_tenant = Tenant(
            name=f"T-{uuid.uuid4().hex[:8]}",
            slug=f"t-{uuid.uuid4().hex[:8]}",
            is_active=True,
            tier="enterprise",
            join_code=uuid.uuid4().hex[:12],
        )
        db.session.add(other_tenant)
        db.session.flush()
        other_user = _make_user("investigator", tenant_id=other_tenant.id)
        db.session.commit()
        client = _login_as(app.test_client(), other_user)
        resp = client.post(
            _findings_json(case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 403

    def test_other_case_finding_404(self, auth_client):
        case, _, finding = _scaffold()
        other_case = _make_case()
        resp = auth_client.post(
            _findings_json(other_case.id, finding.id) + "/verify",
            json={"status": "verified"},
        )
        assert resp.status_code == 404
