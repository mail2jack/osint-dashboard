"""Tests for financial records and comments modules."""

from datetime import datetime, timezone


# ─── Financial Records ─────────────────────────────────────────────────────


class TestFinancialRecordCreate:
    def test_requires_auth(self, client):
        resp = client.post(
            "/cms/financials/create",
            json={
                "case_id": "x",
                "transaction_date": "2024-01-01",
                "amount": 100,
            },
        )
        assert resp.status_code in (302, 401)

    def test_create_financial_record(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.post(
            "/cms/financials/create",
            json={
                "case_id": str(case_id),
                "transaction_date": "2024-06-15",
                "amount": 2500.50,
                "transaction_type": "income",
                "description": "Test payment",
                "counterparty_name": "ACME Corp",
            },
        )
        assert resp.status_code == 201, resp.get_json()
        data = resp.get_json()
        assert data.get("record", {}).get("amount") == 2500.50

    def test_create_financial_missing_fields(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.post(
            "/cms/financials/create",
            json={
                "case_id": str(case_id),
            },
        )
        assert resp.status_code == 400

    def test_financial_summary(self, auth_client, app):
        from cms import db
        from cms.models import FinancialRecord

        cid, case_id = _create_client_case(db)
        rec = FinancialRecord(
            case_id=case_id,
            transaction_date=datetime.now(timezone.utc).date(),
            amount=500,
            transaction_type="income",
        )
        db.session.add(rec)
        db.session.commit()
        resp = auth_client.get(f"/cms/cases/{case_id}/financial-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        summary = data.get("summary", {})
        assert summary.get("total_amount", 0) >= 500

    def test_verify_financial_record(self, auth_client, app):
        from cms import db
        from cms.models import FinancialRecord

        cid, case_id = _create_client_case(db)
        rec = FinancialRecord(
            case_id=case_id,
            transaction_date=datetime.now(timezone.utc).date(),
            amount=100,
            transaction_type="expense",
        )
        db.session.add(rec)
        db.session.commit()
        resp = auth_client.post(
            f"/cms/financials/{rec.id}/verify",
            json={
                "action": "verify",
                "notes": "Checked against bank statement",
            },
        )
        assert resp.status_code == 200
        assert rec.verification_status == "verified"

    def test_verify_financial_nonexistent(self, auth_client):
        resp = auth_client.post(
            "/cms/financials/nonexistent/verify", json={"action": "verify"}
        )
        assert resp.status_code == 404


# ─── Comments ──────────────────────────────────────────────────────────────


class TestComments:
    def test_requires_auth(self, client):
        resp = client.post("/cms/api/comments", json={"content": "hi", "case_id": "x"})
        assert resp.status_code in (302, 401)

    def test_create_comment(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.post(
            "/cms/api/comments",
            json={
                "content": "Test comment",
                "case_id": str(case_id),
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data.get("content") == "Test comment"

    def test_create_comment_missing_content(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.post(
            "/cms/api/comments",
            json={
                "case_id": str(case_id),
            },
        )
        assert resp.status_code == 400

    def test_get_comments_for_entity(self, auth_client, app):
        from cms import db
        from cms.models import Comment

        cid, case_id = _create_client_case(db)
        c = Comment(content="Entity comment", case_id=case_id, author_id=1)
        db.session.add(c)
        db.session.commit()
        resp = auth_client.get(f"/cms/api/comments/for-entity?type=case&id={case_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("count", 0) >= 1

    def test_get_comment_count(self, auth_client, app):
        from cms import db
        from cms.models import Comment

        cid, case_id = _create_client_case(db)
        c = Comment(content="Count test", case_id=case_id, author_id=1)
        db.session.add(c)
        db.session.commit()
        resp = auth_client.get(f"/cms/api/comments/count?type=case&id={case_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("count", 0) >= 1

    def test_edit_comment(self, auth_client, app):
        from cms import db
        from cms.models import Comment

        cid, case_id = _create_client_case(db)
        c = Comment(content="Original", case_id=case_id, author_id=1)
        db.session.add(c)
        db.session.commit()
        resp = auth_client.put(
            f"/cms/api/comments/{c.id}",
            json={
                "content": "Updated content",
            },
        )
        assert resp.status_code == 200
        assert c.content == "Updated content"

    def test_delete_comment(self, auth_client, app):
        from cms import db
        from cms.models import Comment

        cid, case_id = _create_client_case(db)
        c = Comment(content="To delete", case_id=case_id, author_id=1)
        db.session.add(c)
        db.session.commit()
        resp = auth_client.delete(f"/cms/api/comments/{c.id}")
        assert resp.status_code == 200
        assert c.is_deleted is True


# ─── Exports ───────────────────────────────────────────────────────────────


class TestExportRequiresAuth:
    def test_case_export_requires_auth(self, client, app):
        resp = client.get("/cms/cases/some-id/export")
        assert resp.status_code in (302, 401)

    def test_subjects_export_requires_auth(self, client):
        resp = client.get("/cms/subjects/export")
        assert resp.status_code in (302, 401)

    def test_cases_export_requires_auth(self, client):
        resp = client.get("/cms/cases/export")
        assert resp.status_code in (302, 401)

    def test_clients_export_requires_auth(self, client):
        resp = client.get("/cms/clients/export")
        assert resp.status_code in (302, 401)


class TestExports:
    def test_export_single_case_csv(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.get(f"/cms/cases/{case_id}/export?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_export_subjects_csv(self, auth_client, app):
        from cms import db
        from cms.models import Subject

        db.session.add(Subject(name="ExportTest", subject_type="person"))
        db.session.commit()
        resp = auth_client.get("/cms/subjects/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_export_clients_csv(self, auth_client, app):
        from cms import db
        from cms.models import Client

        db.session.add(
            Client(
                name="ExportClient",
                contact_person="T",
                contact_email="t@t.nl",
                is_active=True,
            )
        )
        db.session.commit()
        resp = auth_client.get("/cms/clients/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_export_cases_csv(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.get("/cms/cases/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_export_unsupported_format(self, auth_client, app):
        from cms import db

        cid, case_id = _create_client_case(db)
        resp = auth_client.get(f"/cms/cases/{case_id}/export?format=pdf")
        assert resp.status_code == 400


# ─── System Routes ─────────────────────────────────────────────────────────


class TestSystem:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("status") == "ok"

    def test_health_quick(self, client):
        resp = client.get("/health?quick=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("status") == "ok"

    def test_version(self, client):
        resp = client.get("/api/version")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "version" in data

    def test_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ai_available" in data

    def test_root_redirect(self, client):
        resp = client.get("/")
        assert resp.status_code == 302

    def test_rate_limit_status(self, client):
        resp = client.get("/api/rate-limit-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "api_rate_limits" in data


# ─── Help System ───────────────────────────────────────────────────────────


class TestHelp:
    def test_help_index_requires_auth(self, client):
        resp = client.get("/cms/help")
        assert resp.status_code in (302, 401)

    def test_help_index(self, auth_client):
        resp = auth_client.get("/cms/help")
        assert resp.status_code == 200

    def test_help_topic(self, auth_client):
        resp = auth_client.get("/cms/help/cases")
        assert resp.status_code == 200

    def test_help_topic_invalid(self, auth_client):
        resp = auth_client.get("/cms/help/doesnotexist")
        assert resp.status_code == 404

    def test_help_api_topic(self, auth_client):
        resp = auth_client.get("/cms/api/help/subjects")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "html" in data

    def test_help_api_topic_invalid(self, auth_client):
        resp = auth_client.get("/cms/api/help/doesnotexist")
        assert resp.status_code == 404


# ─── Helpers ───────────────────────────────────────────────────────────────


def _create_client_case(db):
    from cms.models import Client, Case

    client = Client(
        name="TestCorp", contact_person="T", contact_email="t@t.nl", is_active=True
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"TC-{datetime.now(timezone.utc).timestamp():.0f}",
        client_id=client.id,
        title="Test",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    db.session.commit()
    return client.id, case.id
