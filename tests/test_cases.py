class TestCaseList:
    URL = "/cms/cases"

    def test_list_cases_requires_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302

    def test_list_cases_empty(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200

    def test_list_cases_with_search(self, auth_client):
        resp = auth_client.get(self.URL + "?search=test")
        assert resp.status_code == 200


class TestCaseCreate:
    URL = "/cms/cases/create"

    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Test Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    def _default_body(self, auth_client):
        return {
            "title": "Test Case",
            "client_id": self._create_client(auth_client),
            "description": "A test case",
            "priority": "high",
        }

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={"title": "x", "client_id": "x"})
        assert resp.status_code == 401

    def test_create_case(self, auth_client, db_session):
        resp = auth_client.post(self.URL, json=self._default_body(auth_client))
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["case"]["title"] == "Test Case"
        assert data["case"]["priority"] == "high"

    def test_create_case_missing_client(self, auth_client):
        body = self._default_body(auth_client)
        body["client_id"] = ""
        resp = auth_client.post(self.URL, json=body)
        assert resp.status_code == 400

    def test_create_case_invalid_priority(self, auth_client):
        body = {**self._default_body(auth_client), "priority": "invalid"}
        resp = auth_client.post(self.URL, json=body)
        assert resp.status_code == 400

    def test_list_shows_created_case(self, auth_client, db_session):
        auth_client.post(self.URL, json=self._default_body(auth_client))
        resp = auth_client.get("/cms/cases")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Test Case" in html


class TestCaseEdit:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Edit Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    def _create_case(self, auth_client):
        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Editable Case",
                "client_id": client_id,
            },
        )
        return resp.get_json()["case"]["id"]

    def test_edit_title(self, auth_client, db_session):
        case_id = self._create_case(auth_client)
        resp = auth_client.post(
            f"/cms/cases/{case_id}/edit",
            json={
                "title": "Updated Title",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["case"]["title"] == "Updated Title"

    def test_edit_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/cases/nonexistent/edit", json={"title": "x"})
        assert resp.status_code == 404

    def test_edit_priority(self, auth_client, db_session):
        case_id = self._create_case(auth_client)
        resp = auth_client.post(
            f"/cms/cases/{case_id}/edit",
            json={
                "priority": "low",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["case"]["priority"] == "low"


class TestCaseArchive:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Archive Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    def _create_and_close_case(self, auth_client, db_session):
        from cms.models import Case

        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Archive Test",
                "client_id": client_id,
            },
        )
        case_id = resp.get_json()["case"]["id"]
        case = db_session.get(Case, case_id)
        case.status = "closed"
        db_session.commit()
        return case_id

    def test_archive_closed_case(self, auth_client, db_session):
        case_id = self._create_and_close_case(auth_client, db_session)
        resp = auth_client.post(f"/cms/cases/{case_id}/archive")
        assert resp.status_code == 302

    def test_archive_open_case_fails(self, auth_client, db_session):
        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Open Case",
                "client_id": client_id,
            },
        )
        case_id = resp.get_json()["case"]["id"]
        resp = auth_client.post(f"/cms/cases/{case_id}/archive")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


class TestCaseBulkDelete:
    URL = "/cms/api/cases/bulk-delete"

    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "BulkDelete Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    def test_bulk_delete_empty(self, auth_client):
        resp = auth_client.post(self.URL, json={"ids": []})
        assert resp.status_code == 400

    def test_bulk_delete_case(self, auth_client, db_session):
        from cms.models import Case

        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Delete Me",
                "client_id": client_id,
            },
        )
        case_id = resp.get_json()["case"]["id"]
        resp = auth_client.post(self.URL, json={"ids": [case_id]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] == 1
        case = db_session.get(Case, case_id)
        assert case.is_deleted is True


class TestCaseAuditLog:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "AuditLog Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    def test_create_logs_audit(self, auth_client, db_session):
        from cms.models import AuditLog

        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Audit Logged",
                "client_id": client_id,
            },
        )
        case_id = resp.get_json()["case"]["id"]
        logs = AuditLog.query.filter_by(entity_type="case", entity_id=case_id).all()
        assert len(logs) >= 1
        assert logs[0].action == "create"
