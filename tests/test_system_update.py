import pytest

from cms.models import Setting


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


class TestUpdateCheckRepoValidation:
    def test_is_github_repo_valid(self):
        from cms.routes.system import _is_github_repo

        assert _is_github_repo("mail2jack/osint-dashboard")
        assert _is_github_repo("owner/repo.name-123")
        assert not _is_github_repo("")
        assert not _is_github_repo("owner")
        assert not _is_github_repo("owner/repo/extra")
        assert not _is_github_repo("https://github.com/owner/repo")
        assert not _is_github_repo("../owner/repo")
        assert not _is_github_repo("owner/../repo")
        assert not _is_github_repo("owner/repo?x=1")
        assert not _is_github_repo("owner/repo#frag")

    def test_check_update_rejects_invalid_repo(self, app, auth_client):
        Setting.set("update_check_repo", "https://evil.example.com/../../steal")
        r = auth_client.get("/cms/api/check-update")
        assert r.status_code == 200
        data = r.get_json()
        assert data["check_enabled"] is False
        assert "invalid" in data["message"]

    def test_check_update_accepts_valid_repo_without_network(
        self, app, auth_client, monkeypatch
    ):
        from cms.routes import system as system_mod

        Setting.set("update_check_repo", "mail2jack/osint-dashboard")
        calls = []

        def fake_fetch(url, **kwargs):
            calls.append(url)
            raise RuntimeError("no network in tests")

        monkeypatch.setattr(system_mod, "jittered_get", fake_fetch)
        r = auth_client.get("/cms/api/check-update?force=1")
        assert r.status_code == 200
        assert calls, "update check should attempt the remote VERSION fetch"
        assert calls[0] == (
            "https://raw.githubusercontent.com/mail2jack/osint-dashboard/master/VERSION"
        )
