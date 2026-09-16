"""Tests for the canonical photo-analysis storage helper and upload route.

Covers the P1 fix for PHOTO_ANALYSIS_500_PREEXISTING:
- storage root resolution / never under the static tree,
- magic-byte validation of the upload before any write,
- relative data_value (no absolute local path leaking into the DB/UI),
- atomic failure handling (rollback + file cleanup),
- worker resolution of current + historical data_value values.
"""

import io
import os
import struct
import zlib
from unittest.mock import patch

import pytest

from cms.services import photo_storage
from cms.services.photo_storage import (
    MAX_PHOTO_FILE_BYTES,
    allowed_detected_format,
    photo_analysis_dir,
    resolve_photo_path,
    sweep_stale_photos,
    unlink_photo,
    validate_photo,
)


def _make_png_bytes():
    """Minimal valid 1x1 PNG."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

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


@pytest.fixture
def photo_root(tmp_path, monkeypatch):
    """Point the canonical root at a temp dir for the whole test."""
    monkeypatch.setattr(photo_storage, "photo_analysis_dir", lambda: str(tmp_path))
    monkeypatch.setattr(photo_storage, "ensure_photo_analysis_dir", lambda: str(tmp_path))
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


class TestStorageRoot:
    def test_dir_lives_under_instance_not_static(self):
        assert "static" not in photo_analysis_dir()
        assert "photos" not in photo_analysis_dir()

    def test_store_and_resolve_roundtrip(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png")
        assert name.endswith(".png")
        assert "/" not in name and "\\" not in name
        assert os.path.isfile(os.path.join(str(photo_root), name))
        resolved = resolve_photo_path(name)
        assert resolved == os.path.realpath(os.path.join(str(photo_root), name))

    def test_data_value_must_be_relative(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png")
        assert not os.path.isabs(name)


class TestResolvePhotoPath:
    def test_none_and_empty(self, photo_root):
        assert resolve_photo_path(None) is None
        assert resolve_photo_path("") is None

    def test_missing_file_returns_none(self, photo_root):
        assert resolve_photo_path("does-not-exist.png") is None

    def test_traversal_rejected(self, photo_root):
        assert resolve_photo_path("../outside.png") is None
        assert resolve_photo_path("/etc/passwd") is None

    def test_absolute_path_inside_root(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "jpg")
        full = os.path.join(str(photo_root), name)
        assert resolve_photo_path(full) == os.path.realpath(full)

    def test_historical_cms_static_path_rejected(self, photo_root):
        # Old rows stored /opt/osint-dashboard/cms/static/uploads/photos/...
        assert resolve_photo_path("/opt/osint-dashboard/cms/static/uploads/photos/x.jpg") is None


class TestUnlinkPhoto:
    def test_removes_single_file(self, photo_root):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png")
        unlink_photo(name)
        assert not os.path.exists(os.path.join(str(photo_root), name))

    def test_never_touches_outside_root(self, photo_root, tmp_path):
        outside = tmp_path.parent / "photo-storage-outside"
        outside.mkdir(exist_ok=True)
        target = outside / "keep.txt"
        target.write_text("keep")
        unlink_photo(str(target))
        assert target.exists()

    def test_missing_is_silent(self, photo_root):
        unlink_photo("never-existed.png")  # no raise


class TestSweepStalePhotos:
    def test_ignores_fresh_files(self, photo_root):
        photo_storage.store_photo(_make_fake(_make_png_bytes()), "png")
        assert sweep_stale_photos() == 0

    def test_removes_stale_file(self, photo_root, monkeypatch):
        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "png")
        old = os.path.join(str(photo_root), name)
        os.utime(old, (os.path.getmtime(old) - 10 * 3600,) * 2)
        assert sweep_stale_photos() == 1
        assert not os.path.exists(old)


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

    def test_upload_success_stores_relative_data_value(self, auth_client, workflow_case, photo_root):
        from cms.models import ResearchAction

        resp = self._post(auth_client, workflow_case.id, files={"photo": (io.BytesIO(_make_png_bytes()), "fake.png")})
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "started"

        action = ResearchAction.query.get(body["id"])
        assert action is not None
        assert action.action_type == "photo_analysis"
        assert not os.path.isabs(action.data_value)
        stored = os.path.join(str(photo_root), action.data_value)
        assert os.path.isfile(stored)

    def test_rejects_non_image_content(self, auth_client, workflow_case):
        resp = self._post(auth_client, workflow_case.id, files={"photo": (io.BytesIO(b"not an image"), "fake.txt")})
        assert resp.status_code == 400

    def test_rejects_oversized_upload(self, auth_client, workflow_case):
        blob = _make_png_bytes() + b"\x00" * MAX_PHOTO_FILE_BYTES
        resp = self._post(auth_client, workflow_case.id, files={"photo": (io.BytesIO(blob), "big.png")})
        assert resp.status_code == 413

    def test_rejects_duplicate_running_action(self, auth_client, workflow_case, photo_root):
        from cms.models import ResearchAction

        first = self._post(auth_client, workflow_case.id, files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")})
        assert first.status_code == 200
        action = ResearchAction.query.get(first.get_json()["id"])
        action.status = "running"
        from cms import db
        db.session.commit()

        resp = self._post(auth_client, workflow_case.id, files={"photo": (io.BytesIO(_make_png_bytes()), "two.png")})
        assert resp.status_code == 409

    def test_invalid_subject_rejected_before_save(self, auth_client, workflow_case, photo_root):
        resp = self._post(
            auth_client,
            workflow_case.id,
            files={"photo": (io.BytesIO(_make_png_bytes()), "one.png")},
            form={"subject_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 404
        assert list(photo_root.iterdir()) == []


class TestWorkerPhotoResolution:
    def test_adhoc_upload_cleaned_after_analysis(self, photo_root, monkeypatch):
        from cms.workflow.actions.other_action import _photo_analysis

        name = photo_storage.store_photo(_make_fake(_make_png_bytes()), "jpg")
        with patch(
            "cms.services.photo_analysis.analyze_photo",
            return_value={"gps": None, "camera": {}, "datetime": None, "software": None, "privacy": "lower"},
        ), patch(
            "cms.services.photo_analysis.format_analysis_finding",
            return_value={"title": "ok", "source_type": "photo_analysis", "verified": False},
        ), patch(
            "cms.workflow.actions.helpers._action_subject",
            return_value=None,
        ):
            from cms.models import ResearchAction

            action = ResearchAction(
                action_type="photo_analysis",
                data_value=name,
                status="pending",
            )
            findings = _photo_analysis(action)
        assert findings and findings[0]["title"] == "ok"
        assert not os.path.exists(os.path.join(str(photo_root), name))

    def test_historical_cms_static_value_no_unavailable_no_500(self, photo_root):
        from cms.workflow.actions.other_action import _photo_analysis
        from cms.models import ResearchAction

        action = ResearchAction(
            action_type="photo_analysis",
            data_value="/opt/osint-dashboard/cms/static/uploads/photos/x.jpg",
            status="pending",
        )
        findings = _photo_analysis(action)
        assert findings
        assert "No photo available" in findings[0]["title"]