"""Integration tests for webhooks, API keys, and background tasks."""

import json
from unittest.mock import patch, MagicMock


# =============================================================================
# Webhook tests
# =============================================================================


class TestWebhookDispatch:
    @staticmethod
    def _tenant_settings():
        from flask import g
        from cms.models import TenantSetting, User

        tenant_id = User.query.filter_by(username="admin").first().tenant_id
        g.tenant_id = tenant_id
        return TenantSetting, tenant_id

    def test_dispatch_no_urls(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            TenantSetting, tenant_id = self._tenant_settings()

            TenantSetting.set("webhook_urls", [], tenant_id=tenant_id, category="system", encrypt=False)
            TenantSetting.set("webhook_secret", "", tenant_id=tenant_id, category="system", encrypt=False)

            results = dispatch("subject.create", {"id": "1"})
            assert results == []

    def test_dispatch_uses_requested_tenant_configuration(self, app):
        from cms.models import Tenant, TenantSetting, User, db
        from cms.webhooks import dispatch

        with app.app_context():
            tenant_a = User.query.filter_by(username="admin").first().tenant_id
            tenant_b = Tenant(
                name="Webhook tenant B",
                slug="webhook-tenant-b",
                join_code="webhook-tenant-b-code",
            )
            db.session.add(tenant_b)
            db.session.flush()
            TenantSetting.set(
                "webhook_urls", ["https://tenant-a.example/"], tenant_id=tenant_a
            )
            TenantSetting.set(
                "webhook_urls", ["https://tenant-b.example/"], tenant_id=tenant_b.id
            )

            with patch("httpx.post") as mock_post:
                response = MagicMock(is_success=True, status_code=200)
                mock_post.return_value = response
                dispatch("subject.created", {"id": "1"}, tenant_id=tenant_a)

            assert mock_post.call_args.args[0] == "https://tenant-a.example/"

    def test_dispatch_with_hmac(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            TenantSetting, tenant_id = self._tenant_settings()

            TenantSetting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                tenant_id=tenant_id,
                category="system",
                encrypt=False,
            )
            TenantSetting.set(
                "webhook_secret", "test-secret", tenant_id=tenant_id, category="system", encrypt=False
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
            TenantSetting, tenant_id = self._tenant_settings()

            TenantSetting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                tenant_id=tenant_id,
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
            TenantSetting, tenant_id = self._tenant_settings()

            TenantSetting.set(
                "webhook_urls",
                ["https://h1.example.com/", "https://h2.example.com/"],
                tenant_id=tenant_id,
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
            TenantSetting, tenant_id = self._tenant_settings()

            TenantSetting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                tenant_id=tenant_id,
                category="system",
                encrypt=False,
            )
            TenantSetting.set("webhook_secret", "", tenant_id=tenant_id, category="system", encrypt=False)

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

    def test_dispatch_does_not_place_webhook_secret_in_body(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            TenantSetting, tenant_id = self._tenant_settings()

            TenantSetting.set(
                "webhook_urls",
                ["https://hooks.example.com/"],
                tenant_id=tenant_id,
                category="system",
                encrypt=False,
            )
            TenantSetting.set(
                "webhook_secret", "secret-must-stay-in-header", tenant_id=tenant_id, category="system", encrypt=False
            )

            with patch("httpx.post") as mock_post:
                mock_response = MagicMock()
                mock_response.is_success = True
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                dispatch("subject.create", {"id": "1"})
                body = mock_post.call_args.kwargs["content"]
                headers = mock_post.call_args.kwargs["headers"]
                assert "secret-must-stay-in-header" not in body
                assert "X-Webhook-Signature" in headers

    def test_dispatch_ignores_invalid_or_credential_bearing_destinations(self, app):
        from cms.webhooks import dispatch

        with app.app_context():
            TenantSetting, tenant_id = self._tenant_settings()
            TenantSetting.set(
                "webhook_urls",
                [
                    "ftp://unsupported.example/",
                    "https://127.0.0.1/",
                    "https://10.0.0.8/",
                    "https://localhost/",
                    "not-a-url",
                    "https://user:password@example.com/",
                    "https://valid.example/",
                    "https://valid.example/",
                ],
                tenant_id=tenant_id,
                category="system",
                encrypt=False,
            )

            with patch("httpx.post") as mock_post:
                response = MagicMock(is_success=True, status_code=200)
                mock_post.return_value = response
                results = dispatch("subject.created", {"id": "1"})

            assert len(results) == 1
            assert mock_post.call_args.args[0] == "https://valid.example/"


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
            admin = User.query.filter_by(username="admin").first()
            user = User(
                username="junior",
                email="junior@test.nl",
                full_name="Junior",
                role="junior_investigator",
                tenant_id=admin.tenant_id if admin else None,
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
    def test_rq_enqueue_contract(self, monkeypatch):
        import sys
        import types

        import cms.background as background

        captured = {}

        class FakeConnection:
            def close(self):
                captured["closed"] = True

        class FakeQueue:
            def __init__(self, name, connection):
                captured["queue"] = (name, connection)

            def enqueue(self, path, **kwargs):
                captured["enqueue"] = (path, kwargs)

        fake_redis = types.SimpleNamespace(
            from_url=lambda url, socket_connect_timeout: (
                captured.update({"url": url, "timeout": socket_connect_timeout})
                or FakeConnection()
            )
        )
        fake_rq = types.SimpleNamespace(Queue=FakeQueue)
        monkeypatch.setitem(sys.modules, "redis", fake_redis)
        monkeypatch.setitem(sys.modules, "rq", fake_rq)
        monkeypatch.setattr(background, "_use_rq", True)
        monkeypatch.setattr(background, "_RQ_URL", "redis://rq.example/0")

        def sample_task(value):
            return value

        assert background._enqueue_rq("task-1", sample_task, 42, flag=True)
        assert captured["url"] == "redis://rq.example/0"
        assert captured["timeout"] == 3
        assert captured["queue"][0] == "default"
        path, kwargs = captured["enqueue"]
        assert path == "cms.tasks.run_background_task"
        assert kwargs == {
            "task_id": "task-1",
            "func_module": __name__,
            "func_name": "TestBackgroundTasks.test_rq_enqueue_contract.<locals>.sample_task",
            "args": (42,),
            "kwargs": {"flag": True},
        }
        assert captured["closed"] is True

    def test_thread_task_restores_persisted_tenant_context(self, app):
        from flask import g

        from cms.background import get_task_status, run_in_background
        from cms.models import User

        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            assert admin is not None
            observed = []

            def capture_context():
                observed.append(getattr(g, "tenant_id", None))
                return "ok"

            task_id = "test-task-tenant-context"
            with app.test_request_context():
                g.tenant_id = admin.tenant_id
                run_in_background(task_id, capture_context)

            import time

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = get_task_status(task_id)
                if observed and status and status["status"] == "completed":
                    break
                time.sleep(0.05)

            assert observed == [admin.tenant_id]
            assert get_task_status(task_id)["status"] == "completed"

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
