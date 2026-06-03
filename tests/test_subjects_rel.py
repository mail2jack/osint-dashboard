"""Tests for subject relationship endpoints."""


def _create_subject(auth_client, name="Test Subject"):
    resp = auth_client.post(
        "/cms/subjects/create",
        json={"name": name, "subject_type": "person"},
    )
    return resp.get_json()["subject"]["id"]


class TestSubjectRelationships:
    def test_get_empty_relationships(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.get(f"/cms/subjects/{subj_id}/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subject"]["id"] == subj_id
        assert len(data["nodes"]) == 1
        assert data["edges"] == []

    def test_add_relationship(self, auth_client):
        subj_a = _create_subject(auth_client, "Subject A")
        subj_b = _create_subject(auth_client, "Subject B")
        resp = auth_client.post(
            f"/cms/subjects/{subj_a}/add-relationship",
            json={"related_subject_id": subj_b, "relationship_type": "family"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["relationship"]["bidirectional"] is True

    def test_add_relationship_self(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/add-relationship",
            json={"related_subject_id": subj_id},
        )
        assert resp.status_code == 400

    def test_add_relationship_nonexistent_target(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/add-relationship",
            json={"related_subject_id": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_add_duplicate_relationship(self, auth_client):
        subj_a = _create_subject(auth_client, "A")
        subj_b = _create_subject(auth_client, "B")
        auth_client.post(
            f"/cms/subjects/{subj_a}/add-relationship",
            json={"related_subject_id": subj_b},
        )
        resp = auth_client.post(
            f"/cms/subjects/{subj_a}/add-relationship",
            json={"related_subject_id": subj_b},
        )
        assert resp.status_code == 400

    def test_relationship_shows_in_get(self, auth_client):
        subj_a = _create_subject(auth_client, "Alice")
        subj_b = _create_subject(auth_client, "Bob")
        auth_client.post(
            f"/cms/subjects/{subj_a}/add-relationship",
            json={"related_subject_id": subj_b, "relationship_type": "partner"},
        )
        resp = auth_client.get(f"/cms/subjects/{subj_a}/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["nodes"]) == 2

    def test_remove_relationship(self, auth_client):
        subj_a = _create_subject(auth_client, "A")
        subj_b = _create_subject(auth_client, "B")
        auth_client.post(
            f"/cms/subjects/{subj_a}/add-relationship",
            json={"related_subject_id": subj_b},
        )
        resp = auth_client.post(
            f"/cms/subjects/{subj_a}/remove-relationship",
            json={"related_subject_id": subj_b},
        )
        assert resp.status_code == 200

    def test_remove_relationship_no_id(self, auth_client):
        subj_id = _create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/remove-relationship",
            json={},
        )
        assert resp.status_code == 400
