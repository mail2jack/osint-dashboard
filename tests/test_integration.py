"""Integration tests for webhooks, API keys, and background tasks."""

import json
from unittest.mock import patch, MagicMock


# =============================================================================
# Webhook tests
# =============================================================================


class TestWebhookDispatch:
    def test_dispatch_no_urls(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            from cms.models import Setting

            Setting.set("webhook_urls", [], category="system", encrypt=False)
            Setting.set("webhook_secret", "", category="system", encrypt=False)

            results = dispatch("subject.create", {"id": "1"})
            assert results == []

    def test_dispatch_with_hmac(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            from cms.models import Setting

            Setting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                category="system",
                encrypt=False,
            )
            Setting.set(
                "webhook_secret", "test-secret", category="system", encrypt=False
            )

            with patch("httpx.post") as mock_post:
                mock_response = MagicMock()
                mock_response.is_success = True
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                results = dispatch("subject.create", {"id": "1"})
                assert len(results) == 1
                assert results[0]["ok"] is True

                # Verify HMAC header was sent
                call_args = mock_post.call_args
                assert call_args is not None
                headers = call_args.kwargs["headers"]
                assert "X-Webhook-Signature" in headers
                assert headers["Content-Type"] == "application/json"

    def test_dispatch_failure(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            from cms.models import Setting

            Setting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                category="system",
                encrypt=False,
            )

            with patch("httpx.post") as mock_post:
                mock_post.side_effect = Exception("Connection refused")

                results = dispatch("subject.create", {"id": "1"})
                assert len(results) == 1
                assert results[0]["ok"] is False
                assert "Connection refused" in results[0]["error"]

    def test_dispatch_multiple_urls(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            from cms.models import Setting

            Setting.set(
                "webhook_urls",
                ["https://h1.example.com/", "https://h2.example.com/"],
                category="system",
                encrypt=False,
            )

            with patch("httpx.post") as mock_post:
                mock_response = MagicMock()
                mock_response.is_success = True
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                results = dispatch("subject.create", {"id": "1"})
                assert len(results) == 2
                assert mock_post.call_count == 2

    def test_dispatch_payload_structure(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            from cms.models import Setting

            Setting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                category="system",
                encrypt=False,
            )
            Setting.set("webhook_secret", "", category="system", encrypt=False)

            with patch("httpx.post") as mock_post:
                mock_response = MagicMock()
                mock_response.is_success = True
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                dispatch("subject.create", {"id": "1", "name": "test"})
                call_args = mock_post.call_args
                body = json.loads(call_args.kwargs["content"])
                assert body["event"] == "subject.create"
                assert body["payload"]["id"] == "1"
                assert body["payload"]["name"] == "test"
                assert "timestamp" in body


# =============================================================================
# API Key tests
# =============================================================================


class TestApiKeys:
    def test_list_keys_empty(self, auth_client):
        resp = auth_client.get("/cms/api/api-keys")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []

    def test_generate_key(self, auth_client):
        resp = auth_client.post("/cms/api/api-keys/generate", json={"name": "test-key"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "test-key"
        assert "key" in data
        assert data["key"].startswith(data["prefix"])

    def test_list_keys_after_generate(self, auth_client):
        auth_client.post("/cms/api/api-keys/generate", json={"name": "list-test"})
        resp = auth_client.get("/cms/api/api-keys")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1
        names = [k["name"] for k in data]
        assert "list-test" in names

    def test_generate_key_requires_name(self, auth_client):
        resp = auth_client.post("/cms/api/api-keys/generate", json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_revoke_key(self, auth_client):
        gen = auth_client.post(
            "/cms/api/api-keys/generate", json={"name": "revoke-test"}
        )
        key_id = gen.get_json()["id"]
        resp = auth_client.post(f"/cms/api/api-keys/{key_id}/revoke")
        assert resp.status_code == 200

    def test_delete_key(self, auth_client):
        gen = auth_client.post(
            "/cms/api/api-keys/generate", json={"name": "delete-test"}
        )
        key_id = gen.get_json()["id"]
        resp = auth_client.post(f"/cms/api/api-keys/{key_id}/delete")
        assert resp.status_code == 200

    def test_revoke_nonexistent_key(self, auth_client):
        resp = auth_client.post("/cms/api/api-keys/nonexistent/revoke")
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/cms/api/api-keys")
        assert resp.status_code in (302, 401)

    def test_requires_admin(self, app, client):
        from cms.models import db, User

        with app.app_context():
            user = User(
                username="junior",
                email="junior@test.nl",
                full_name="Junior",
                role="junior_investigator",
                is_active=True,
            )
            user.set_password("Test1234!")
            db.session.add(user)
            db.session.commit()

        with client.session_transaction() as sess:
            from cms.models import User as U

            u = U.query.filter_by(username="junior").first()
            sess["_user_id"] = str(u.id)
            sess["_fresh"] = True
            sess["_remember"] = "set"

        resp = client.post("/cms/api/api-keys/generate", json={"name": "should-fail"})
        assert resp.status_code in (302, 403)


# =============================================================================
# Background task tests
# =============================================================================


class TestBackgroundTasks:
    def test_run_and_get_status(self, app):
        from cms.background import run_in_background, get_task_status

        def dummy():
            return 42

        with app.app_context():
            task_id = "test-task-1"
            run_in_background(task_id, dummy)
            status = get_task_status(task_id)
            assert status is not None
            assert status["status"] in ("pending", "running", "completed")
            assert status["task_name"] == "dummy"

    def test_get_nonexistent_task(self, app):
        from cms.background import get_task_status

        with app.app_context():
            status = get_task_status("nonexistent-task")
            assert status is None

    def test_failed_task(self, app):
        from cms.background import run_in_background, get_task_status

        def failing():
            raise ValueError("oops")

        with app.app_context():
            task_id = "test-task-fail"
            run_in_background(task_id, failing)
            import time

            time.sleep(1.0)
            status = get_task_status(task_id)
            if status:
                assert status["status"] in ("running", "failed")

    def test_task_status_endpoint(self, app, client):
        from cms.background import run_in_background

        def dummy():
            return "done"

        with app.app_context():
            task_id = "test-task-endpoint"
            run_in_background(task_id, dummy)

        resp = client.get(f"/cms/api/background/status/{task_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data["task_name"] == "dummy"

    def test_task_status_endpoint_not_found(self, client):
        resp = client.get("/cms/api/background/status/nonexistent")
        assert resp.status_code == 404

    def test_cleanup_old_tasks(self, app):
        from cms.background import cleanup_old_tasks

        with app.app_context():
            count = cleanup_old_tasks(max_age_hours=0)
            assert isinstance(count, int)
