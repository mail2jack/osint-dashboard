import io
from datetime import datetime, timezone


def _create_client_case(db):
    from cms.models import Client, Case

    client = Client(
        name="SS Client", contact_person="T", contact_email="t@t.nl", is_active=True
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"SST-{datetime.now(timezone.utc).timestamp():.0f}",
        client_id=client.id,
        title="Screenshot Test",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    db.session.commit()
    return client.id, case.id


def _make_png_bytes():
    """Create a minimal valid PNG (1x1 pixel) in-memory."""
    import struct
    import zlib

    def chunk(chunk_type, data):
        c = chunk_type + data
        return (
            struct.pack(">I", len(data))
            + c
            + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00\xff\x00\x00"
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


class TestScreenshotList:
    def test_requires_auth(self, client):
        resp = client.get("/cms/cases/x/screenshots")
        assert resp.status_code in (302, 401)

    def test_list_empty(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.get(f"/cms/cases/{case_id}/screenshots")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0


class TestScreenshotUpload:
    def test_requires_auth(self, client):
        resp = client.post("/cms/cases/x/screenshots/upload")
        assert resp.status_code in (302, 401)

    def test_upload_screenshot(self, auth_client, app):
        from cms import db
        from cms.models import Screenshot

        cid, case_id = _create_client_case(db)
        png_data = _make_png_bytes()
        data = {
            "file": (io.BytesIO(png_data), "test.png"),
            "url": "https://example.com",
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/screenshots/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201, resp.get_json()
        result = resp.get_json()
        assert result["screenshot"]["url"] == "https://example.com"
        assert result["screenshot"]["case_id"] == case_id
        saved = db.session.get(Screenshot, result["screenshot"]["id"])
        assert saved is not None

    def test_upload_no_file(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.post(
            f"/cms/cases/{case_id}/screenshots/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "No file provided" in resp.get_json()["error"]

    def test_upload_invalid_file(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        data = {
            "file": (io.BytesIO(b"this is not an image"), "test.txt"),
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/screenshots/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "image" in resp.get_json()["error"].lower()


class TestScreenshotGet:
    def _create_screenshot(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        png_data = _make_png_bytes()
        data = {
            "file": (io.BytesIO(png_data), "test.png"),
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/screenshots/upload",
            data=data,
            content_type="multipart/form-data",
        )
        return case_id, resp.get_json()["screenshot"]["id"]

    def test_get_screenshot(self, auth_client, app):
        case_id, ss_id = self._create_screenshot(auth_client, app)
        resp = auth_client.get(f"/cms/cases/{case_id}/screenshots/{ss_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == ss_id
        assert data["case_id"] == case_id

    def test_get_nonexistent(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.get(f"/cms/cases/{case_id}/screenshots/nonexistent")
        assert resp.status_code == 404


class TestScreenshotDelete:
    def _create_screenshot(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        png_data = _make_png_bytes()
        data = {
            "file": (io.BytesIO(png_data), "test.png"),
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/screenshots/upload",
            data=data,
            content_type="multipart/form-data",
        )
        return case_id, resp.get_json()["screenshot"]["id"]

    def test_delete_screenshot(self, auth_client, app):
        from cms import db
        from cms.models import Screenshot

        case_id, ss_id = self._create_screenshot(auth_client, app)
        resp = auth_client.delete(f"/cms/cases/{case_id}/screenshots/{ss_id}")
        assert resp.status_code == 200
        assert db.session.get(Screenshot, ss_id) is None

    def test_delete_nonexistent(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.delete(f"/cms/cases/{case_id}/screenshots/nonexistent")
        assert resp.status_code == 404
