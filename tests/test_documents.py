import io
from unittest.mock import patch


class TestCaseDocumentUpload:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Doc Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    def _create_case(self, auth_client):
        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Doc Test Case",
                "client_id": client_id,
            },
        )
        return resp.get_json()["case"]["id"]

    @patch("cms.routes.documents.validate_upload")
    def test_upload_document(self, mock_validate, auth_client, db_session):
        mock_validate.return_value = (True, "png")
        case_id = self._create_case(auth_client)

        data = {
            "file": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake-png-data"), "test.png"),
            "document_type": "evidence",
            "description": "Test evidence document",
            "classification": "confidential",
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        doc_data = resp.get_json()["document"]
        assert doc_data["document_type"] == "evidence"
        assert doc_data["description"] == "Test evidence document"

    def test_upload_no_file(self, auth_client, db_session):
        client_id = self._create_client(auth_client)
        resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "No File Case",
                "client_id": client_id,
            },
        )
        case_id = resp.get_json()["case"]["id"]
        resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data={"document_type": "evidence"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_empty_filename(self, auth_client, db_session):
        case_id = self._create_case(auth_client)
        data = {
            "file": (io.BytesIO(b"data"), ""),
            "document_type": "evidence",
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    @patch("cms.routes.documents.validate_upload")
    def test_upload_disallowed_extension(self, mock_validate, auth_client, db_session):
        case_id = self._create_case(auth_client)
        data = {
            "file": (io.BytesIO(b'<?php exec("id"); ?>'), "shell.php"),
            "document_type": "evidence",
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    @patch("cms.routes.documents.validate_upload")
    def test_upload_content_type_mismatch(self, mock_validate, auth_client, db_session):
        mock_validate.return_value = (False, "text/plain")
        case_id = self._create_case(auth_client)
        data = {
            "file": (io.BytesIO(b"not-a-real-png"), "test.png"),
            "document_type": "evidence",
        }
        resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "content does not match" in resp.get_json()["error"].lower()


class TestSubjectDocumentUpload:
    def _create_subject(self, auth_client):
        resp = auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Doc Subject",
                "subject_type": "person",
            },
        )
        return resp.get_json()["subject"]["id"]

    @patch("cms.routes.documents.validate_upload")
    def test_upload_subject_document(self, mock_validate, auth_client, db_session):
        mock_validate.return_value = (True, "pdf")
        subj_id = self._create_subject(auth_client)
        data = {
            "file": (io.BytesIO(b"%PDF-1.4 fake-pdf"), "report.pdf"),
            "document_type": "report",
            "description": "Subject report",
        }
        resp = auth_client.post(
            f"/cms/subjects/{subj_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        doc_data = resp.get_json()["document"]
        assert doc_data["document_type"] == "report"


class TestDocumentRetrieve:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Retrieve Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    @patch("cms.routes.documents.validate_upload")
    def test_get_document(self, mock_validate, auth_client, db_session):
        mock_validate.return_value = (True, "png")
        client_id = self._create_client(auth_client)
        case_resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Doc Get Test",
                "client_id": client_id,
            },
        )
        case_id = case_resp.get_json()["case"]["id"]
        data = {
            "file": (io.BytesIO(b"\x89PNG\r\n\x1a\ndata"), "test.png"),
        }
        upload_resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        doc_id = upload_resp.get_json()["document"]["id"]
        resp = auth_client.get(f"/cms/documents/{doc_id}")
        assert resp.status_code == 200
        doc_data = resp.get_json()
        assert doc_data["id"] == doc_id

    def test_get_nonexistent(self, auth_client):
        resp = auth_client.get("/cms/documents/nonexistent")
        assert resp.status_code == 404


class TestDocumentDelete:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Delete Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    @patch("cms.routes.documents.validate_upload")
    def test_delete_document(self, mock_validate, auth_client, db_session):
        mock_validate.return_value = (True, "png")
        from cms.models import Document

        client_id = self._create_client(auth_client)
        case_resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Doc Delete Test",
                "client_id": client_id,
            },
        )
        case_id = case_resp.get_json()["case"]["id"]
        data = {
            "file": (io.BytesIO(b"\x89PNG\r\n\x1a\ndata"), "del.png"),
        }
        upload_resp = auth_client.post(
            f"/cms/cases/{case_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )
        doc_id = upload_resp.get_json()["document"]["id"]
        resp = auth_client.delete(f"/cms/documents/{doc_id}")
        assert resp.status_code == 200
        doc = db_session.get(Document, doc_id)
        assert doc is None

    def test_delete_nonexistent(self, auth_client):
        resp = auth_client.delete("/cms/documents/nonexistent")
        assert resp.status_code == 404


class TestCaseDocumentsList:
    def _create_client(self, auth_client):
        resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "List Client",
                "is_company": True,
            },
        )
        return resp.get_json()["client"]["id"]

    @patch("cms.routes.documents.validate_upload")
    def test_list_case_documents(self, mock_validate, auth_client, db_session):
        mock_validate.return_value = (True, "png")
        client_id = self._create_client(auth_client)
        case_resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Doc List Test",
                "client_id": client_id,
            },
        )
        case_id = case_resp.get_json()["case"]["id"]
        for i in range(3):
            data = {
                "file": (io.BytesIO(b"\x89PNG\r\n\x1a\ndata"), f"doc{i}.png"),
            }
            auth_client.post(
                f"/cms/cases/{case_id}/upload",
                data=data,
                content_type="multipart/form-data",
            )
        resp = auth_client.get(f"/cms/cases/{case_id}/documents")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["documents"]) == 3
