"""Tests for subject face encoding and photo endpoints."""


def _make_face_encoding():
    return [0.5 + i * 0.001 for i in range(128)]


def _create_subject(auth_client, name="Face Subject"):
    resp = auth_client.post(
        "/cms/subjects/create",
        json={"name": name, "subject_type": "person"},
    )
    return resp.get_json()["subject"]["id"]


class TestSaveFaceEncoding:
    VALID_ENCODING = _make_face_encoding()

    def test_save_encoding(self, auth_client, db_session):
        from cms.models import Subject

        subj_id = _create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/face-encoding",
            json={"encoding": self.VALID_ENCODING},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_encoding"] is True
        subj = db_session.get(Subject, subj_id)
        assert subj.face_encoding == self.VALID_ENCODING

    def test_save_encoding_empty(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/face-encoding",
            json={"encoding": []},
        )
        assert resp.status_code == 400

    def test_save_encoding_invalid_length(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/face-encoding",
            json={"encoding": [0.1, 0.2, 0.3]},
        )
        assert resp.status_code == 400


class TestDeleteFaceEncoding:
    def _save_encoding(self, auth_client, subj_id):
        encoding = _make_face_encoding()
        return auth_client.post(
            f"/cms/subjects/{subj_id}/face-encoding",
            json={"encoding": encoding},
        )

    def test_delete_encoding(self, auth_client, db_session):
        from cms.models import Subject

        subj_id = _create_subject(auth_client)
        self._save_encoding(auth_client, subj_id)
        resp = auth_client.delete(f"/cms/subjects/{subj_id}/face-encoding")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_encoding"] is False
        subj = db_session.get(Subject, subj_id)
        assert subj.face_encoding is None

    def test_delete_encoding_no_stored(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.delete(f"/cms/subjects/{subj_id}/face-encoding")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_encoding"] is False

    def test_delete_encoding_nonexistent(self, auth_client):
        resp = auth_client.delete("/cms/subjects/nonexistent/face-encoding")
        assert resp.status_code in (302, 401, 404)


class TestCompareFaces:
    def test_compare_invalid_encoding(self, auth_client):
        resp = auth_client.post(
            "/cms/subjects/compare-faces",
            json={"encoding": [0.1, 0.2]},
        )
        assert resp.status_code == 400

    def test_compare_no_matches(self, auth_client):
        encoding = _make_face_encoding()
        resp = auth_client.post(
            "/cms/subjects/compare-faces",
            json={"encoding": encoding},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("matches") == []
