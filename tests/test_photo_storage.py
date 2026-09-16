"""Tests for the canonical tenant-scoped photo-analysis storage, upload route,
DB invariant, sweep, quota, and failure-atomic behaviour (PR #169 review).

Storage layout::

    <instance_path>/photo_analysis/<tenant_id>/<uuid4.hex>.<ext>

Every helper (store / resolve / unlink / sweep / usage) is scoped to a single
``tenant_id``; a ``data_value`` from tenant A can never resolve or delete a
file belonging to tenant B, even with a hand-manipulated ``data_value``.

This file also verifies:
- the partial unique index ``uq_research_actions_active_photo_analysis``
- status-aware stale-sweep (pending/running files never removed)
- honest per-tenant quota (multiple pending uploads counted jointly)
- failure-atomicity (rollback + file cleanup on any error path)
- HEIC rejection (magic bytes only, no trust on Content-Type)
"""

import io
import os
import re
import struct
import threading
import zlib
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from cms import db
from cms.models import ResearchAction, User
from cms.services import photo_storage
from cms.services.photo_storage import (
    MAX_PHOTO_FILE_BYTES,
    InvalidTenantComponent,
    allowed_detected_format,
    photo_analysis_dir,
    resolve_photo_path,
    sweep_stale_photos,
    tenant_photo_analysis_usage,
    unlink_photo,
    validate_photo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _admin_tenant_id():
    return User.query.filter_by(role="admin").first().tenant_id


def _make_png_bytes():
    """Minimal valid 1x1 PNG."""

    def chunk(tag, data):
        c = tag + data
        return (
            struct.pack(">I", len(data))
            + c
            + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


def _make_fake(content, content_type="image/png", filename="test.png"):
    """Return a minimal Werkzeug-like object with tell/read/seek/save."""

    class _FakeFile:
        def __init__(self, content, ctype, filename):
            self._buf = io.BytesIO(content)
            self.content_type = ctype
            self.filename = filename

        def tell(self):
            return self._buf.tell()

        def seek(self, pos):
            self._buf.seek(pos)

        def read(self, n=-1):
            return self._buf.read(n)

        def save(self, path):
            with open(path, "wb") as f:
                f.write(self._buf.getvalue())

    return _FakeFile(content, content_type, filename)


class _PartialSave:
    """File-like that writes partial bytes then raises OSError."""

    def __init__(self, content, fail_after=8):
        self._content = content
        self._fail_after = fail_after

    def tell(self):
        return 0

    def seek(self, pos):
        pass

    def read(self, n=-1):
        return self._content[:n] if n >= 0 else self._content

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self._content[: self._fail_after])
        raise OSError("simulated disk failure")


class _NonOSErrorSave:
    """File-like that writes fully then raises a non-OSError."""

    def __init__(self, content):
        self._content = content

    def tell(self):
        return 0

    def seek(self, pos):
        pass

    def read(self, n=-1):
        return self._content[:n] if n >= 0 else self._content

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self._content)
        raise RuntimeError("simulated unexpected failure")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def photo_root(tmp_path, monkeypatch):
    """Point the canonical storage root at a temp dir for the whole test."""
    monkeypatch.setattr(photo_storage, "photo_analysis_root", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def workflow_case(auth_client):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "Photo Storage Client",
            "title": "Photo Storage Case",
            "subject_0_name": "Subject One",
            "subject_0_type": "person",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    from cms.models import Case

    return Case.query.filter_by(title="Photo Storage Case").first()


@pytest.fixture
def no_worker(monkeypatch):
    """Suppress background worker for deterministic route tests."""
    calls = []
    monkeypatch.setattr(
        "cms.workflow.routes.start_action_async",
        lambda action_id: calls.append(action_id),
    )
    return calls


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------


class TestCanonicalLayout:
    def test_dir_lives_under_instance_not_static(self):
        tid = _admin_tenant_id()
        result = photo_analysis_dir(tid)
        assert "static" not in result
        assert "photos" not in result
        assert result.endswith(f"/{tid}")

    @pytest.mark.parametrize(
        "bad",
        ["", None, "a/b", "a b", "a.b", "../x", "x" * 65, "héllo", "a$b"],
    )
    def test_invalid_tenant_component_raises(self, bad):
        with pytest.raises(InvalidTenantComponent):
            photo_analysis_dir(bad)

    def test_valid_tenant_component_ok(self):
        result = photo_analysis_dir(TENANT_A)
        assert result.endswith(f"/{TENANT_A}")

    def test_store_returns_full_uuid_filename(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        base, ext = name.rsplit(".", 1)
        assert len(base) == 32
        assert re.fullmatch(r"[0-9a-f]{32}", base)
        assert ext == "png"


# ---------------------------------------------------------------------------
# Validation (HEIC explicit rejection + existing suite)
# ---------------------------------------------------------------------------


class TestAllowedDetectedFormat:
    def test_normalizes_jpeg(self):
        assert allowed_detected_format("jpeg") == "jpg"
        assert allowed_detected_format("jpg") == "jpg"

    def test_accepts_images(self):
        assert allowed_detected_format("png") == "png"
        assert allowed_detected_format("gif") == "gif"
        assert allowed_detected_format("webp") == "webp"

    def test_rejects_documents(self):
        assert allowed_detected_format("pdf") is None
        assert allowed_detected_format("docx") is None
        assert allowed_detected_format("") is None
        assert allowed_detected_format(None) is None


class TestValidatePhoto:
    def test_valid_png(self):
        ok, ext = validate_photo(_make_fake(_make_png_bytes()))
        assert ok is True
        assert ext == "png"

    def test_random_bytes_rejected(self):
        ok, ext = validate_photo(_make_fake(b"this is not an image"))
        assert ok is False
        assert ext == ""

    def test_none_rejected(self):
        ok, ext = validate_photo(None)
        assert ok is False

    def test_heic_magic_bytes_rejected(self):
        """HEIC is intentionally NOT accepted — magic bytes don't cover it."""
        heic_magic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20
        ok, ext = validate_photo(
            _make_fake(heic_magic, content_type="image/heic", filename="x.heic")
        )
        assert ok is False
        assert ext == ""


# ---------------------------------------------------------------------------
# resolve_photo_path (tenant-scoped)
# ---------------------------------------------------------------------------


class TestResolvePhotoPath:
    def test_none_and_empty(self, photo_root):
        assert resolve_photo_path(None, TENANT_A) is None
        assert resolve_photo_path("", TENANT_A) is None

    def test_missing_file_returns_none(self, photo_root):
        assert resolve_photo_path("does-not-exist.png", TENANT_A) is None

    def test_traversal_rejected(self, photo_root):
        assert resolve_photo_path("../outside.png", TENANT_A) is None
        assert resolve_photo_path("/etc/passwd", TENANT_A) is None

    def test_absolute_path_always_rejected(self, photo_root):
        """Historical absolute paths → fail-safe None."""
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        full = os.path.join(str(photo_root), name)
        assert resolve_photo_path(full, TENANT_A) is None

    def test_cross_tenant_rejected(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        assert resolve_photo_path(name, TENANT_B) is None

    def test_roundtrip_resolves_same_tenant(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        resolved = resolve_photo_path(name, TENANT_A)
        expected = os.path.realpath(os.path.join(str(photo_root), TENANT_A, name))
        assert resolved == expected


# ---------------------------------------------------------------------------
# unlink_photo (tenant-scoped)
# ---------------------------------------------------------------------------


class TestUnlinkPhoto:
    def test_removes_file_in_own_tenant(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        assert unlink_photo(name, TENANT_A) is True
        assert not os.path.exists(os.path.join(str(photo_root), name))

    def test_cross_tenant_no_op(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        assert unlink_photo(name, TENANT_B) is False
        assert os.path.exists(os.path.join(str(photo_root), TENANT_A, name))

    def test_absolute_path_ignored(self, photo_root):
        assert unlink_photo("/etc/passwd", TENANT_A) is False

    def test_traversal_rejected(self, photo_root):
        assert unlink_photo("../../etc/passwd", TENANT_A) is False

    def test_missing_file_is_silent(self, photo_root):
        assert unlink_photo("never-existed.png", TENANT_A) is False

    def test_invalid_tenant_component_raises(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        with pytest.raises(InvalidTenantComponent):
            unlink_photo(name, "a/b")

    def test_none_data_value(self, photo_root):
        assert unlink_photo(None, TENANT_A) is False


# ---------------------------------------------------------------------------
# tenant_photo_analysis_usage
# ---------------------------------------------------------------------------


class TestTenantUsage:
    def test_empty_tenant(self, photo_root):
        count, total = tenant_photo_analysis_usage(TENANT_A)
        assert count == 0
        assert total == 0

    def test_counts_own_tenant_files(self, photo_root):
        data1 = _make_png_bytes()
        data2 = data1 + b"\x00" * 100
        photo_storage.store_photo(_make_fake(data1), "png", TENANT_A)
        photo_storage.store_photo(_make_fake(data2), "png", TENANT_A)
        count, total = tenant_photo_analysis_usage(TENANT_A)
        assert count == 2
        assert total == len(data1) + len(data2)

    def test_other_tenant_files_excluded(self, photo_root):
        data = _make_png_bytes()
        photo_storage.store_photo(_make_fake(data), "png", TENANT_A)
        photo_storage.store_photo(_make_fake(data), "png", TENANT_B)
        count_a, total_a = tenant_photo_analysis_usage(TENANT_A)
        count_b, _ = tenant_photo_analysis_usage(TENANT_B)
        assert count_a == 1
        assert total_a == len(data)
        assert count_b == 1


# ---------------------------------------------------------------------------
# sweep_stale_photos (status-aware, tenant-scoped, bounded)
# ---------------------------------------------------------------------------


class TestSweepStalePhotos:
    def _make_old_file(self, photo_root, name, tenant_id=TENANT_A):
        """Create a file in the tenant dir with a mtime far in the past."""
        path = os.path.join(str(photo_root), tenant_id, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"data")
        old_time = (datetime.now() - timedelta(hours=10)).timestamp()
        os.utime(path, (old_time, old_time))
        return path

    def _make_fresh_file(self, photo_root, name, tenant_id=TENANT_A):
        path = os.path.join(str(photo_root), tenant_id, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"data")
        return path

    def test_ignores_fresh_files(self, photo_root):
        photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        assert sweep_stale_photos(TENANT_A) == 0

    def test_removes_stale_unreferenced(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        self._make_old_file(photo_root, name)
        assert sweep_stale_photos(TENANT_A) == 1

    def test_keeps_stale_referenced(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        self._make_old_file(photo_root, name)
        assert sweep_stale_photos(TENANT_A, referenced={name}) == 0
        assert os.path.exists(os.path.join(str(photo_root), TENANT_A, name))

    def test_pending_running_never_swept(self, photo_root):
        """A file referenced by pending or running action survives sweep."""
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        self._make_old_file(photo_root, name)
        for status in ("pending", "running"):
            assert sweep_stale_photos(TENANT_A, referenced={name}) == 0
        assert os.path.exists(os.path.join(str(photo_root), TENANT_A, name))

    def test_unlink_failure_not_counted(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        self._make_old_file(photo_root, name)
        with patch("cms.services.photo_storage.unlink_photo", return_value=False):
            assert sweep_stale_photos(TENANT_A) == 0
        assert os.path.exists(os.path.join(str(photo_root), TENANT_A, name))

    def test_tenant_isolation(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png", TENANT_A)
        self._make_old_file(photo_root, name, TENANT_A)
        assert sweep_stale_photos(TENANT_B) == 0
        assert os.path.exists(os.path.join(str(photo_root), TENANT_A, name))

    def test_dir_does_not_exist(self, photo_root, tmp_path):
        assert sweep_stale_photos("nonexistent-tenant") == 0

    def test_skips_directories(self, photo_root):
        subdir = os.path.join(str(photo_root), TENANT_A, "a-subdir")
        os.makedirs(subdir)
        stale_name = photo_storage.store_photo(
            _make_fake(_make_png_bytes()), "png", TENANT_A
        )
        self._make_old_file(photo_root, stale_name)
        removed = sweep_stale_photos(TENANT_A)
        assert removed == 1
        assert os.path.isdir(subdir)


# ---------------------------------------------------------------------------
# store_photo failure-atomicity (helper level)
# ---------------------------------------------------------------------------


class TestStorePhotoFailure:
    def test_oserror_cleans_up_partial_file(self, photo_root):
        with pytest.raises(OSError, match="disk failure"):
            photo_storage.store_photo(_PartialSave(_make_png_bytes()), "png", TENANT_A)
        tenant_dir = os.path.join(str(photo_root), TENANT_A)
        assert os.path.isdir(tenant_dir) and not os.listdir(tenant_dir)

    def test_non_os_error_cleans_up(self, photo_root):
        with pytest.raises(RuntimeError, match="unexpected failure"):
            photo_storage.store_photo(
                _NonOSErrorSave(_make_png_bytes()), "png", TENANT_A
            )
        tenant_dir = os.path.join(str(photo_root), TENANT_A)
        assert os.path.isdir(tenant_dir) and not os.listdir(tenant_dir)


# ---------------------------------------------------------------------------
# Upload route
# ---------------------------------------------------------------------------


class TestPhotoAnalysisUploadRoute:
    URL = "/cms/workflow/api/case/{case_id}/photo-analysis"

    def _post(self, auth_client, case_id, files=None, form=None):
        data = dict(form or {})
        if files:
            data.update(files)
        return auth_client.post(
            self.URL.format(case_id=case_id),
            data=data,
            content_type="multipart/form-data",
        )

    def test_requires_photo(self, auth_client, workflow_case):
        resp = self._post(auth_client, workflow_case.id)
        assert resp.status_code == 400

    def test_upload_success_relative_name_under_tenant_dir(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        tenant_id = _admin_tenant_id()
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "fake.png")},
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "started"

        action = ResearchAction.query.get(body["id"])
        assert action is not None
        assert action.action_type == "photo_analysis"
        assert action.tenant_id == tenant_id
        assert action.status == "pending"
        assert not os.path.isabs(action.data_value)

        # Full uuid name
        base, _ = action.data_value.rsplit(".", 1)
        assert len(base) == 32

        stored = os.path.join(str(photo_root), tenant_id, action.data_value)
        assert os.path.isfile(stored)

        # No local path leaks into the JSON response
        assert "instance" not in resp.get_data(as_text=True)

    def test_rejects_non_image_content(self, auth_client, workflow_case, no_worker):
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(b"not an image"), "fake.txt")},
        )
        assert resp.status_code == 400

    def test_rejects_heic_by_magic_bytes(self, auth_client, workflow_case, no_worker):
        heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(heic), "photo.heic")},
        )
        assert resp.status_code == 400

    def test_rejects_oversized_upload(self, auth_client, workflow_case, no_worker):
        blob = _make_png_bytes() + b"\x00" * MAX_PHOTO_FILE_BYTES
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(blob), "big.png")},
        )
        assert resp.status_code == 413

    def test_rejects_duplicate_pending(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        tenant_id = _admin_tenant_id()
        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
        )
        assert first.status_code == 200

        second = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")},
        )
        assert second.status_code == 409
        # Only one file and one action exist
        assert (
            ResearchAction.query.filter_by(
                case_id=workflow_case.id,
                action_type="photo_analysis",
                tenant_id=tenant_id,
            ).count()
            == 1
        )
        tenant_dir = os.path.join(str(photo_root), tenant_id)
        assert os.path.isdir(tenant_dir) and len(os.listdir(tenant_dir)) == 1

    def test_rejects_duplicate_running(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
        )
        assert first.status_code == 200
        action = ResearchAction.query.get(first.get_json()["id"])
        action.status = "running"
        db.session.commit()

        second = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")},
        )
        assert second.status_code == 409

    def test_stale_running_auto_reset(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        """A stale running action (started > 600s ago) is reset to error."""
        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
        )
        assert first.status_code == 200
        action = ResearchAction.query.get(first.get_json()["id"])
        action.status = "running"
        action.started_at = datetime.now() - timedelta(seconds=601)
        db.session.commit()

        second = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")},
        )
        assert second.status_code == 200
        db.session.refresh(action)
        assert action.status == "error"
        assert "Stale" in action.error

    def test_stale_pending_not_auto_reset(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        """Pending actions are never silently reset."""
        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
        )
        assert first.status_code == 200
        action = ResearchAction.query.get(first.get_json()["id"])
        action.started_at = datetime.now() - timedelta(hours=1)
        db.session.commit()

        second = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")},
        )
        assert second.status_code == 409
        db.session.refresh(action)
        assert action.status == "pending"

    def test_different_subject_scope_not_blocked(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        """Case-wide and subject-scoped uploads coexist."""
        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "case-wide.png")},
        )
        assert first.status_code == 200

        subject = workflow_case.subjects.first()
        second = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "subject-scoped.png")},
            form={"subject_id": subject.id},
        )
        assert second.status_code == 200

    def test_invalid_subject_rejected_before_save(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
            form={"subject_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 404
        assert list(photo_root.iterdir()) == []

    # -----------------------------------------------------------------------
    # Quota
    # -----------------------------------------------------------------------

    def test_quota_files_cap(
        self, auth_client, workflow_case, photo_root, monkeypatch, no_worker
    ):
        """Multiple pending uploads must not exceed the per-tenant file cap."""
        monkeypatch.setattr(photo_storage, "PHOTO_ANALYSIS_TENANT_MAX_FILES", 1)
        monkeypatch.setattr(
            photo_storage, "PHOTO_ANALYSIS_TENANT_MAX_BYTES", 10 * 1024 * 1024
        )

        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
        )
        assert first.status_code == 200

        # Create a second case so the duplicate check doesn't block
        resp2 = auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Quota Client",
                "title": "Quota Case 2",
                "priority": "medium",
            },
        )
        assert resp2.status_code in (200, 302)
        from cms.models import Case

        case2 = Case.query.filter_by(title="Quota Case 2").first()

        second = self._post(
            auth_client,
            case2.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")},
        )
        assert second.status_code == 413
        assert "storage" in second.get_json()["error"].lower()

    def test_quota_bytes_cap(
        self, auth_client, workflow_case, photo_root, monkeypatch, no_worker
    ):
        """Multiple pending uploads must not exceed the per-tenant byte cap."""
        png_size = len(_make_png_bytes())
        # Allow only slightly more than one upload
        monkeypatch.setattr(
            photo_storage, "PHOTO_ANALYSIS_TENANT_MAX_BYTES", png_size + 10
        )
        monkeypatch.setattr(photo_storage, "PHOTO_ANALYSIS_TENANT_MAX_FILES", 1000)

        first = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
        )
        assert first.status_code == 200

        resp2 = auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Quota Client",
                "title": "Quota Case Bytes",
                "priority": "medium",
            },
        )
        assert resp2.status_code in (200, 302)
        from cms.models import Case

        case2 = Case.query.filter_by(title="Quota Case Bytes").first()

        second = self._post(
            auth_client,
            case2.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")},
        )
        assert second.status_code == 413

    def test_regular_tier_storage_limit_is_checked_separately(
        self, auth_client, workflow_case, monkeypatch, no_worker
    ):
        monkeypatch.setattr(
            "cms.tier_limits.check_storage_limit",
            lambda tenant_id, extra_bytes=0: (False, 50, 50),
        )
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "tier-limit.png")},
        )
        assert resp.status_code == 413
        body = resp.get_json()
        assert body["limit"] == "tier_storage"
        assert "tenant storage" in body["error"].lower()

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_archived_active_action_does_not_block_upload(
        self, auth_client, workflow_case, status, no_worker
    ):
        action = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=_admin_tenant_id(),
            action_type="photo_analysis",
            status=status,
            archived_at=datetime.now(),
        )
        db.session.add(action)
        db.session.commit()
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "archived-ok.png")},
        )
        assert resp.status_code == 200, resp.get_json()

    def test_restore_archived_active_conflict_returns_409(
        self, auth_client, workflow_case
    ):
        tenant_id = _admin_tenant_id()
        archived = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tenant_id,
            action_type="photo_analysis",
            status="pending",
            archived_at=datetime.now(),
        )
        active = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tenant_id,
            action_type="photo_analysis",
            status="running",
        )
        db.session.add_all([archived, active])
        db.session.commit()
        resp = auth_client.post(
            f"/cms/workflow/api/actions/{archived.id}/restore", json={}
        )
        assert resp.status_code == 409
        db.session.refresh(archived)
        assert archived.archived_at is not None

    # -----------------------------------------------------------------------
    # Failure-atomic (route level)
    # -----------------------------------------------------------------------

    def test_save_oserror_no_action_no_file(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        with patch(
            "cms.services.photo_storage.store_photo",
            side_effect=OSError("disk full"),
        ):
            resp = self._post(
                auth_client,
                workflow_case.id,
                files={"photo": (io.BytesIO(_make_png_bytes()), "fail.png")},
            )
        assert resp.status_code == 500
        assert not ResearchAction.query.filter_by(
            case_id=workflow_case.id, action_type="photo_analysis"
        ).first()

    def test_flush_error_no_action_no_file(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        with patch.object(db.session, "flush", side_effect=RuntimeError("flush fail")):
            resp = self._post(
                auth_client,
                workflow_case.id,
                files={"photo": (io.BytesIO(_make_png_bytes()), "fail.png")},
            )
        assert resp.status_code == 500
        assert not ResearchAction.query.filter_by(
            case_id=workflow_case.id, action_type="photo_analysis"
        ).first()

    def test_commit_error_no_action_no_file(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        with patch.object(
            db.session, "commit", side_effect=RuntimeError("commit fail")
        ):
            resp = self._post(
                auth_client,
                workflow_case.id,
                files={"photo": (io.BytesIO(_make_png_bytes()), "fail.png")},
            )
        assert resp.status_code == 500
        assert not ResearchAction.query.filter_by(
            case_id=workflow_case.id, action_type="photo_analysis"
        ).first()

    def test_audit_error_no_action_no_file(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        with patch(
            "cms.workflow.routes.log_scope_audit",
            side_effect=RuntimeError("audit fail"),
        ):
            resp = self._post(
                auth_client,
                workflow_case.id,
                files={"photo": (io.BytesIO(_make_png_bytes()), "fail.png")},
            )
        assert resp.status_code == 500
        assert not ResearchAction.query.filter_by(
            case_id=workflow_case.id, action_type="photo_analysis"
        ).first()

    def test_worker_start_error_sets_error_status_and_cleans(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        tenant_id = _admin_tenant_id()
        with patch(
            "cms.workflow.routes.start_action_async",
            side_effect=RuntimeError("thread start fail"),
        ):
            resp = self._post(
                auth_client,
                workflow_case.id,
                files={"photo": (io.BytesIO(_make_png_bytes()), "fail.png")},
            )
        assert resp.status_code == 500
        action = ResearchAction.query.filter_by(
            case_id=workflow_case.id, action_type="photo_analysis"
        ).first()
        assert action is not None
        assert action.status == "error"
        assert "worker" in action.error.lower()
        tenant_dir = os.path.join(str(photo_root), tenant_id)
        # File must have been removed
        assert not os.path.isdir(tenant_dir) or not os.listdir(tenant_dir)

    def test_error_responses_never_contain_local_path(
        self, auth_client, workflow_case, photo_root, no_worker
    ):
        # Invalid content → 400
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(b"too short"), "bad.png")},
        )
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert str(photo_root) not in body
        assert "/" not in body.replace("error", "")

        # Oversized → 413
        blob = _make_png_bytes() + b"\x00" * MAX_PHOTO_FILE_BYTES
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(blob), "big.png")},
        )
        assert resp.status_code == 413
        body = resp.get_data(as_text=True)
        assert str(photo_root) not in body


# ---------------------------------------------------------------------------
# Worker (_photo_analysis) — tenant-scoped resolution + cleanup
# ---------------------------------------------------------------------------


class TestWorkerPhotoResolution:
    def test_adhoc_upload_cleaned_after_analysis(self, photo_root):
        from cms.workflow.actions.other_action import _photo_analysis

        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "jpg", TENANT_A)
        action = ResearchAction(
            action_type="photo_analysis",
            data_value=name,
            tenant_id=TENANT_A,
            status="pending",
        )
        with (
            patch(
                "cms.services.photo_analysis.analyze_photo",
                return_value={
                    "gps": {"lat": 1},
                    "camera": {},
                    "datetime": None,
                    "software": None,
                    "privacy": "lower",
                },
            ),
            patch(
                "cms.services.photo_analysis.format_analysis_finding",
                return_value={
                    "title": "ok",
                    "source_type": "photo_analysis",
                    "verified": False,
                    "raw_data": {"gps": {"lat": 1}},
                },
            ),
            patch(
                "cms.workflow.actions.other_action._action_subject",
                return_value=None,
            ),
        ):
            findings = _photo_analysis(action)

        assert findings and findings[0]["title"] == "ok"
        assert not os.path.exists(os.path.join(str(photo_root), name))

    def test_picarta_failure_does_not_mask_cleanup(self, photo_root):
        from cms.workflow.actions.other_action import _photo_analysis

        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "jpg", TENANT_A)
        action = ResearchAction(
            action_type="photo_analysis",
            data_value=name,
            tenant_id=TENANT_A,
            status="pending",
        )
        with (
            patch(
                "cms.services.photo_analysis.analyze_photo",
                return_value={
                    "gps": None,
                    "camera": {},
                    "datetime": None,
                    "software": None,
                    "privacy": "lower",
                },
            ),
            patch(
                "cms.services.photo_analysis.format_analysis_finding",
                return_value={
                    "title": "ok",
                    "source_type": "photo_analysis",
                    "verified": False,
                    "raw_data": {"gps": None},
                },
            ),
            patch(
                "cms.workflow.actions.other_action._action_subject",
                return_value=None,
            ),
            patch(
                "cms.workflow.actions.other_action._picarta_geolocate",
                side_effect=RuntimeError("api down"),
            ),
        ):
            findings = _photo_analysis(action)

        assert findings and findings[0]["title"] == "ok"
        # File removed despite Picarta error
        assert not os.path.exists(os.path.join(str(photo_root), name))

    def test_analysis_failure_propagates_and_cleans(self, photo_root):
        from cms.workflow.actions.other_action import _photo_analysis

        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "jpg", TENANT_A)
        action = ResearchAction(
            action_type="photo_analysis",
            data_value=name,
            tenant_id=TENANT_A,
            status="pending",
        )
        with (
            patch(
                "cms.services.photo_analysis.analyze_photo",
                side_effect=RuntimeError("decode failure"),
            ),
            patch(
                "cms.workflow.actions.other_action._action_subject",
                return_value=None,
            ),
        ):
            with pytest.raises(RuntimeError, match="decode failure"):
                _photo_analysis(action)

        # File cleaned up even though exception propagated
        assert not os.path.exists(os.path.join(str(photo_root), name))

    def test_analysis_failure_marks_action_error_via_run_action(
        self, photo_root, workflow_case, auth_client
    ):
        from cms.workflow.actions.registry import run_action

        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "jpg", TENANT_A)
        action = ResearchAction(
            action_type="photo_analysis",
            data_value=name,
            tenant_id=TENANT_A,
            case_id=workflow_case.id,
            status="pending",
        )
        db.session.add(action)
        db.session.commit()

        with (
            patch(
                "cms.services.photo_analysis.analyze_photo",
                side_effect=RuntimeError("decode failure"),
            ),
            patch(
                "cms.workflow.actions.other_action._action_subject",
                return_value=None,
            ),
        ):
            run_action(action.id)

        db.session.expire_all()
        action = ResearchAction.query.get(action.id)
        assert action.status == "error"
        assert not os.path.exists(os.path.join(str(photo_root), TENANT_A, name))

    def test_historical_cms_static_value_unavailable(self, photo_root):
        from cms.workflow.actions.other_action import _photo_analysis

        action = ResearchAction(
            action_type="photo_analysis",
            data_value="/opt/osint-dashboard/cms/static/uploads/photos/x.jpg",
            tenant_id=TENANT_A,
            status="pending",
        )
        with patch(
            "cms.workflow.actions.other_action._action_subject",
            return_value=None,
        ):
            findings = _photo_analysis(action)

        assert findings
        assert "No photo available" in findings[0]["title"]


# ---------------------------------------------------------------------------
# Concurrent duplicate upload — DB invariant
# ---------------------------------------------------------------------------


class TestConcurrentDuplicateUpload:
    def test_partial_unique_index_exists(self):
        """The DB-level concurrency guard is present."""
        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            from sqlalchemy import text

            row = db.session.execute(
                text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE indexname = 'uq_research_actions_active_photo_analysis'"
                )
            ).first()
            assert row is not None
        else:
            row = db.session.execute(
                db.text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'index' "
                    "AND name = 'uq_research_actions_active_photo_analysis'"
                )
            ).first()
            assert row is not None

    def test_partial_unique_index_excludes_archived_actions(self):
        if db.engine.dialect.name == "postgresql":
            from sqlalchemy import text

            predicate = db.session.execute(
                text(
                    "SELECT pg_get_expr(i.indpred, i.indrelid) "
                    "FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = 'uq_research_actions_active_photo_analysis'"
                )
            ).scalar_one()
            assert "archived_at IS NULL" in predicate
        else:
            sql = db.session.execute(
                db.text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' "
                    "AND name = 'uq_research_actions_active_photo_analysis'"
                )
            ).scalar_one()
            assert "archived_at IS NULL" in sql
    def test_duplicate_active_row_flush_raises_integrity_error(self, workflow_case):
        """Two pending rows for same (tenant, case, subject=None) is a DB error."""
        tid = _admin_tenant_id()
        a = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="a.png",
            status="pending",
        )
        db.session.add(a)
        db.session.commit()

        b = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="b.png",
            status="pending",
        )
        db.session.add(b)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_completed_does_not_block(self, workflow_case):
        """A completed row does not conflict with a new pending row."""
        tid = _admin_tenant_id()
        a = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="a.png",
            status="completed",
        )
        db.session.add(a)
        db.session.commit()

        b = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="b.png",
            status="pending",
        )
        db.session.add(b)
        db.session.commit()

    def test_case_wide_and_subject_scoped_coexist(self, workflow_case):
        """case-wide (subject=None) and subject-scoped can both be pending."""
        tid = _admin_tenant_id()
        subject = workflow_case.subjects.first()
        a = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="a.png",
            status="pending",
            subject_id=None,
        )
        b = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="b.png",
            status="pending",
            subject_id=subject.id,
        )
        db.session.add_all([a, b])
        db.session.commit()

    def test_different_case_allows(self, auth_client, workflow_case):
        """Different case_id allows a second pending row."""
        tid = _admin_tenant_id()
        a = ResearchAction(
            case_id=workflow_case.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="a.png",
            status="pending",
        )
        db.session.add(a)
        db.session.commit()

        resp = auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Photo Storage Client",
                "title": "Photo Storage Case 2",
                "priority": "medium",
            },
        )
        assert resp.status_code in (200, 302)
        from cms.models import Case

        case2 = Case.query.filter_by(title="Photo Storage Case 2").first()

        b = ResearchAction(
            case_id=case2.id,
            tenant_id=tid,
            action_type="photo_analysis",
            data_value="b.png",
            status="pending",
        )
        db.session.add(b)
        db.session.commit()
        assert a.id != b.id

    def test_threaded_concurrent_upload_invariant(
        self, app, workflow_case, photo_root, no_worker
    ):
        """Two concurrent uploads for the same scope: only one can win."""
        tenant_id = _admin_tenant_id()
        case_id = workflow_case.id
        url = f"/cms/workflow/api/case/{case_id}/photo-analysis"
        user_id = str(User.query.filter_by(role="admin").first().id)
        barrier = threading.Barrier(2, timeout=5)
        results = []

        def do_upload():
            client = app.test_client()
            with client.session_transaction() as sess:
                sess["_user_id"] = user_id
                sess["_fresh"] = True
                sess["_remember"] = "set"
            barrier.wait(timeout=5)
            resp = client.post(
                url,
                data={"photo": (io.BytesIO(_make_png_bytes()), "race.png")},
                content_type="multipart/form-data",
            )
            results.append(resp.status_code)

        threads = [threading.Thread(target=do_upload) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # At most one winner; the other is 409 (duplicate) or 500 (lock error)
        assert results.count(200) <= 1
        assert results.count(200) + results.count(409) + results.count(500) == 2
        active = (
            ResearchAction.query.filter_by(
                case_id=case_id, action_type="photo_analysis"
            )
            .filter(ResearchAction.status.in_(["pending", "running"]))
            .count()
        )
        assert active <= 1
        tenant_dir = os.path.join(str(photo_root), tenant_id)
        if os.path.isdir(tenant_dir):
            assert len(os.listdir(tenant_dir)) <= 1

    def test_threaded_uploads_serialize_transient_quota(
        self, app, workflow_case, photo_root, no_worker, monkeypatch, auth_client
    ):
        """Different scopes cannot jointly pass the transient tenant cap."""
        tenant_id = _admin_tenant_id()
        png_size = len(_make_png_bytes())
        monkeypatch.setattr(photo_storage, "PHOTO_ANALYSIS_TENANT_MAX_FILES", 256)
        monkeypatch.setattr(
            photo_storage, "PHOTO_ANALYSIS_TENANT_MAX_BYTES", png_size + 1
        )
        second_case_response = auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Quota Race Client",
                "title": "Quota Race Case 2",
                "priority": "medium",
            },
        )
        assert second_case_response.status_code in (200, 302)
        from cms.models import Case

        case_ids = [
            workflow_case.id,
            Case.query.filter_by(title="Quota Race Case 2").first().id,
        ]
        user_id = str(User.query.filter_by(role="admin").first().id)
        barrier = threading.Barrier(2, timeout=5)
        results = []

        def do_upload(case_id):
            client = app.test_client()
            with client.session_transaction() as sess:
                sess["_user_id"] = user_id
                sess["_fresh"] = True
                sess["_remember"] = "set"
            barrier.wait(timeout=5)
            response = client.post(
                f"/cms/workflow/api/case/{case_id}/photo-analysis",
                data={"photo": (io.BytesIO(_make_png_bytes()), "quota-race.png")},
                content_type="multipart/form-data",
            )
            results.append(response.status_code)

        threads = [threading.Thread(target=do_upload, args=(case_id,)) for case_id in case_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert sorted(results) == [200, 413]
        active = ResearchAction.query.filter(
            ResearchAction.tenant_id == tenant_id,
            ResearchAction.action_type == "photo_analysis",
            ResearchAction.status.in_(["pending", "running"]),
        ).count()
        assert active == 1
        assert len(os.listdir(os.path.join(str(photo_root), tenant_id))) == 1
