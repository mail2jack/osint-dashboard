class TestSubjectCreate:
    URL = "/cms/subjects/create"
    CREATE_BODY = {
        "name": "John Doe",
        "subject_type": "person",
    }

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json=self.CREATE_BODY)
        assert resp.status_code == 401

    def test_create_person(self, auth_client, db_session):
        resp = auth_client.post(self.URL, json=self.CREATE_BODY)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["subject"]["name"] == "John Doe"
        assert data["subject"]["subject_type"] == "person"

    def test_create_vehicle(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                "name": "AA-12-BC",
                "subject_type": "vehicle",
                "license_plate": "AA-12-BC",
                "brand": "Toyota",
                "vehicle_type": "car",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["subject"]["subject_type"] == "vehicle"

    def test_create_vessel(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                "name": "MSC Test",
                "subject_type": "vessel",
                "imo_number": "1234567",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["subject"]["subject_type"] == "vessel"

    def test_create_organization(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                "name": "ACME Corp",
                "subject_type": "organization",
                "registration_number": "12345678",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["subject"]["subject_type"] == "organization"

    def test_create_empty_name(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    def test_create_no_type(self, auth_client):
        resp = auth_client.post(self.URL, json={"name": "No Type"})
        assert resp.status_code == 400

    def test_create_with_address(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                "name": "Jane Doe",
                "subject_type": "person",
                "addresses_data": [
                    {
                        "street": "Hoofdstraat",
                        "number": "42",
                        "zipcode": "1234AB",
                        "town": "Amsterdam",
                    }
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert len(data["subject"]["addresses"]) == 1

    def test_create_with_contacts(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                "name": "Jack Smith",
                "subject_type": "person",
                "contacts_data": [
                    {
                        "type": "email",
                        "value": "jack@example.com",
                    }
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert len(data["subject"]["contacts"]) == 1


class TestSubjectEdit:
    def _create_subject(self, auth_client):
        resp = auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Edit Me",
                "subject_type": "person",
            },
        )
        return resp.get_json()["subject"]["id"]

    def test_edit_name(self, auth_client, db_session):
        subj_id = self._create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/edit",
            json={
                "name": "Updated Name",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subject"]["name"] == "Updated Name"

    def test_edit_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/subjects/nonexistent/edit", json={"name": "x"})
        assert resp.status_code == 404

    def test_edit_type(self, auth_client, db_session):
        subj_id = self._create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/edit",
            json={
                "subject_type": "organization",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subject"]["subject_type"] == "organization"

    def test_edit_risk_score(self, auth_client, db_session):
        subj_id = self._create_subject(auth_client)
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/edit",
            json={
                "risk_score": 85,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subject"]["risk_score"] == 85


class TestSubjectDelete:
    def _create_subject(self, auth_client):
        resp = auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Delete Me",
                "subject_type": "person",
            },
        )
        return resp.get_json()["subject"]["id"]

    def test_delete_subject(self, auth_client, db_session):
        from cms.models import Subject

        subj_id = self._create_subject(auth_client)
        resp = auth_client.post(f"/cms/subjects/{subj_id}/delete", json={})
        assert resp.status_code == 200
        subj = db_session.get(Subject, subj_id)
        assert subj.is_deleted is True

    def test_delete_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/subjects/nonexistent/delete", json={})
        assert resp.status_code == 404


class TestSubjectBulkDelete:
    URL = "/cms/api/subjects/bulk-delete"

    def test_bulk_delete_empty(self, auth_client):
        resp = auth_client.post(self.URL, json={"ids": []})
        assert resp.status_code == 400

    def test_bulk_delete_subjects(self, auth_client, db_session):
        from cms.models import Subject

        resp = auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Bulk 1",
                "subject_type": "person",
            },
        )
        subj_id = resp.get_json()["subject"]["id"]
        resp = auth_client.post(self.URL, json={"ids": [subj_id]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] == 1
        subj = db_session.get(Subject, subj_id)
        assert subj.is_deleted is True


class TestSubjectCreateDuplicate:
    def test_create_no_duplicate_check(self, auth_client, db_session):
        auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Duplicate",
                "subject_type": "person",
            },
        )
        resp = auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Duplicate",
                "subject_type": "person",
            },
        )
        assert resp.status_code == 409
