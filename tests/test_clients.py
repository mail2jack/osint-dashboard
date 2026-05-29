class TestClientList:
    URL = "/cms/clients"

    def test_requires_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302

    def test_list_empty(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200

    def test_list_with_search(self, auth_client):
        resp = auth_client.get(self.URL + "?search=test")
        assert resp.status_code == 200


class TestClientCreate:
    URL = "/cms/clients/create"

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={"name": "x"})
        assert resp.status_code == 401

    def test_create_client(self, auth_client, db_session):
        resp = auth_client.post(self.URL, json={"name": "Test Client BV"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["client"]["name"] == "Test Client BV"
        assert data["client"]["is_active"] is True

    def test_create_empty_name(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    def test_create_with_contact(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                "name": "Contact Corp",
                "contact_person": "Jan Janssen",
                "contact_email": "jan@example.com",
                "contact_phone": "+31612345678",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["client"]["contact_person"] == "Jan Janssen"


class TestClientView:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Viewable Client",
            },
        )
        return resp.get_json()["client"]["id"]

    def test_view_client(self, auth_client, db_session):
        client_id = self._create_client(auth_client)
        resp = auth_client.get(f"/cms/clients/{client_id}")
        assert resp.status_code == 200

    def test_view_nonexistent(self, auth_client):
        resp = auth_client.get("/cms/clients/nonexistent")
        assert resp.status_code == 404


class TestClientEdit:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Editable Client",
            },
        )
        return resp.get_json()["client"]["id"]

    def test_edit_name(self, auth_client, db_session):
        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            f"/cms/clients/{client_id}/edit",
            json={
                "name": "Updated Client",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["client"]["name"] == "Updated Client"

    def test_edit_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/clients/nonexistent/edit", json={"name": "x"})
        assert resp.status_code == 404

    def test_edit_all_fields(self, auth_client, db_session):
        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            f"/cms/clients/{client_id}/edit",
            json={
                "name": "Full Update",
                "contact_person": "Piet Pietersen",
                "contact_email": "piet@example.com",
                "vat_number": "NL123456789B01",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["client"]["name"] == "Full Update"


class TestClientDelete:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Delete Me",
            },
        )
        return resp.get_json()["client"]["id"]

    def test_delete_client(self, auth_client, db_session):
        from cms.models import Client

        client_id = self._create_client(auth_client)
        resp = auth_client.post(f"/cms/clients/{client_id}/delete", json={})
        assert resp.status_code == 200
        client = db_session.get(Client, client_id)
        assert client.is_deleted is True

    def test_delete_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/clients/nonexistent/delete", json={})
        assert resp.status_code == 404


class TestClientListShowsCreated:
    def test_list_shows_created_client(self, auth_client, db_session):
        auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Shown Client",
            },
        )
        resp = auth_client.get("/cms/clients")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Shown Client" in html
