import pytest


@pytest.fixture
def update_task():
    return {
        "task_id": "test",
        "status": "done",
        "success": True,
        "results": [
            {
                "step": "Pull latest code",
                "status": "ok",
                "output": "Already up to date.",
            },
            {
                "step": "Apply database migrations",
                "status": "ok",
                "output": "Upgrade successful.",
            },
        ],
    }


class TestUpdateEmail:
    def test_superadmin_and_always_recipient_with_sysinfo(
        self, app, update_task, monkeypatch
    ):
        from cms import email_utils
        from cms.routes import system as system_mod

        sent = []

        class FakeResp:
            text = "203.0.113.7"

        monkeypatch.setattr(system_mod.requests, "get", lambda *a, **k: FakeResp())
        monkeypatch.setattr(email_utils, "is_smtp_configured", lambda: True)
        monkeypatch.setattr(
            email_utils,
            "send_email",
            lambda to, subject, body_html, body_text: (
                sent.append((to, subject, body_html, body_text)) or True
            ),
        )

        system_mod._send_update_email(app, update_task, "v1.0.0")

        emails = [s[0] for s in sent]
        assert "admin@localhost" in emails
        assert "server_update@iveras.com" in emails

        body_html = sent[0][2]
        assert "203.0.113.7" in body_html
        assert "Hostname" in body_html
        assert "Kernel" in body_html
        assert "Iveras update succeeded" in body_html
