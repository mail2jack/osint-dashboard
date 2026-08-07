"""Tests for the Iveras license server (license-server/app.py + licensing.py).

These live outside the dashboard `tests/` dir so the root conftest.py (which
boots the full dashboard app) does not interfere. The license-server's own
`app.py` is loaded via importlib under a unique name to avoid colliding with
the dashboard's `app` module, and the CLI is exercised as a subprocess.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tmp_dir = tempfile.mkdtemp(prefix="license-server-test-")
os.environ["LICENSE_DB_PATH"] = os.path.join(_tmp_dir, "license.db")
os.environ["LICENSE_KEY_PATH"] = os.path.join(_tmp_dir, "private.pem")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-secret"

sys.path.insert(0, _SERVER_DIR)

import licensing  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ls_server_app", os.path.join(_SERVER_DIR, "app.py")
)
ls_app = importlib.util.module_from_spec(_spec)
sys.modules["ls_server_app"] = ls_app
_spec.loader.exec_module(ls_app)
app = ls_app.app
_connect = ls_app._connect

CLI_PY = os.path.join(_SERVER_DIR, "cli.py")


@pytest.fixture(scope="session", autouse=True)
def _keypair():
    licensing.generate_keypair(licensing.PRIVATE_KEY_PATH)
    assert os.path.isfile(licensing.PRIVATE_KEY_PATH)
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    with _connect() as conn:
        conn.execute("DELETE FROM installs")
        conn.execute("DELETE FROM licenses")
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _verify_signature(claims, signature) -> bool:
    from cryptography.exceptions import InvalidSignature

    pub = licensing.load_private_key().public_key()
    try:
        pub.verify(
            licensing._b64dec(signature),
            licensing.canonical_payload(claims).encode("utf-8"),
        )
        return True
    except InvalidSignature:
        return False


def _register(client, install_id="test-install-1", token="tok"):
    return client.post(
        "/api/register",
        json={
            "install_id": install_id,
            "info": {"hostname": "host-1", "app_version": "1.0"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_license(client, install_id="test-install-1", token="tok"):
    return client.get(
        "/api/license",
        headers={"Authorization": f"Bearer {token}", "X-Install-ID": install_id},
    )


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, CLI_PY, *args],
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"
        assert r.get_json()["time"].endswith("Z")


class TestRegister:
    def test_register_issues_trial(self, client):
        r = _register(client)
        assert r.status_code == 200
        body = r.get_json()
        assert body["registered"] is True
        lic = body["license"]
        assert lic["plan"] == "trial"
        assert lic["status"] == "active"
        claims = json.loads(lic["payload"])
        assert claims["install_id"] == "test-install-1"
        assert _verify_signature(claims, lic["signature"])
        expires = datetime.fromisoformat(lic["expires_at"].replace("Z", "+00:00"))
        diff = (expires - datetime.now(timezone.utc)).days
        assert 29 <= diff <= 31

    def test_register_requires_auth(self, client):
        r = client.post("/api/register", json={"install_id": "x"})
        assert r.status_code == 401

    def test_register_idempotent(self, client):
        assert _register(client).get_json()["registered"] is True
        body = _register(client).get_json()
        assert body["registered"] is False
        assert "license" not in body

    def test_register_wrong_token(self, client):
        _register(client, token="correct")
        r = _register(client, token="wrong")
        assert r.status_code == 403

    def test_payload_too_large(self, client):
        r = client.post(
            "/api/register",
            data="x" * 10000,
            headers={"Authorization": "Bearer tok", "Content-Type": "application/json"},
        )
        assert r.status_code == 413


class TestLicenseEndpoint:
    def test_returns_signed_license(self, client):
        _register(client)
        r = _get_license(client)
        assert r.status_code == 200
        body = r.get_json()
        assert body["install_id"] == "test-install-1"
        lic = body["license"]
        claims = json.loads(lic["payload"])
        assert _verify_signature(claims, lic["signature"])
        assert claims["plan"] == "trial"

    def test_unregistered_install(self, client):
        r = _get_license(client)
        assert r.status_code == 404

    def test_wrong_token(self, client):
        _register(client)
        r = _get_license(client, token="wrong")
        assert r.status_code == 403

    def test_missing_auth(self, client):
        _register(client)
        r = client.get("/api/license")
        assert r.status_code == 401

    def test_revoked_status_returned(self, client):
        _register(client)
        proc = _run_cli("license:revoke", "--install", "test-install-1")
        assert proc.returncode == 0
        r = _get_license(client)
        assert r.status_code == 200
        assert r.get_json()["license"]["status"] == "revoked"


class TestTelemetry:
    def test_telemetry_includes_license(self, client):
        _register(client)
        r = client.post(
            "/api/telemetry",
            json={"install_id": "test-install-1", "info": {"cpu_count": 4}},
            headers={"Authorization": "Bearer tok"},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["license"]["plan"] == "trial"

    def test_telemetry_unregistered(self, client):
        r = client.post(
            "/api/telemetry",
            json={"install_id": "nope"},
            headers={"Authorization": "Bearer tok"},
        )
        assert r.status_code == 404


class TestCLI:
    def test_license_new_replaces_trial(self, client):
        _register(client)
        proc = _run_cli(
            "license:new",
            "--install",
            "test-install-1",
            "--plan",
            "full",
            "--days",
            "365",
        )
        assert proc.returncode == 0, proc.stderr
        r = _get_license(client)
        lic = r.get_json()["license"]
        assert lic["plan"] == "full"
        assert lic["status"] == "active"
        with _connect() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM licenses WHERE install_id = ?",
                ("test-install-1",),
            ).fetchone()
        assert rows["n"] == 1

    def test_license_new_unregistered_install(self, client):
        proc = _run_cli(
            "license:new", "--install", "ghost", "--plan", "full", "--days", "365"
        )
        assert proc.returncode == 1
        assert "not registered" in proc.stdout

    def test_license_revoke_no_active(self, client):
        proc = _run_cli("license:revoke", "--install", "ghost")
        assert proc.returncode == 0

    def test_license_list_empty(self, client):
        proc = _run_cli("license:list")
        assert proc.returncode == 0
        assert "No licenses" in proc.stdout


class TestDashboard:
    def test_installs_requires_basic_auth(self, client):
        r = client.get("/api/installs")
        assert r.status_code == 401

    def test_installs_includes_licenses(self, client):
        _register(client)
        r = client.get("/api/installs", auth=("admin", "test-secret"))
        assert r.status_code == 200
        installs = r.get_json()["installs"]
        assert len(installs) == 1
        assert installs[0]["install_id"] == "test-install-1"
        assert installs[0]["license"]["plan"] == "trial"

    def test_dashboard_page(self, client):
        r = client.get("/", auth=("admin", "test-secret"))
        assert r.status_code == 200
        assert "install" in r.get_data(as_text=True).lower()

    def test_dashboard_page_has_actions(self, client):
        r = client.get("/", auth=("admin", "test-secret"))
        body = r.get_data(as_text=True)
        assert "Issue license" in body
        assert "Revoke" in body


class TestWebActions:
    def test_issue_requires_basic_auth(self, client):
        r = client.post("/license/issue", data={"install_id": "x", "plan": "full"})
        assert r.status_code == 401

    def test_issue_full_license(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "full", "days": "365"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        lic = r.get_json()["license"]
        assert lic["plan"] == "full"
        assert lic["status"] == "active"
        claims = json.loads(lic["payload"])
        assert _verify_signature(claims, lic["signature"])
        assert claims["expires_at"].startswith("2")
        assert r.get_json()["status"] == "ok"

    def test_issue_replaces_trial(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "trial", "days": "30"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        assert r.get_json()["license"]["plan"] == "trial"
        with _connect() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM licenses WHERE install_id = ?",
                ("test-install-1",),
            ).fetchone()
        assert rows["n"] == 1

    def test_issue_accepts_expires(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={
                "install_id": "test-install-1",
                "plan": "full",
                "expires": "2027-01-15",
            },
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        assert r.get_json()["license"]["expires_at"] == "2027-01-15T00:00:00Z"

    def test_issue_bad_plan(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "bogus", "days": "365"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 400

    def test_issue_bad_days(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "full", "days": "abc"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 400

    def test_issue_unregistered_install(self, client):
        r = client.post(
            "/license/issue",
            data={"install_id": "ghost", "plan": "full", "days": "365"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 404

    def test_revoke_requires_basic_auth(self, client):
        r = client.post("/license/revoke", data={"install_id": "x"})
        assert r.status_code == 401

    def test_revoke_web(self, client):
        _register(client)
        r = client.post(
            "/license/revoke",
            data={"install_id": "test-install-1"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        assert r.get_json()["revoked"] is True
        lic = _get_license(client).get_json()["license"]
        assert lic["status"] == "revoked"

    def test_revoke_no_active_license(self, client):
        r = client.post(
            "/license/revoke",
            data={"install_id": "ghost"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        assert r.get_json()["revoked"] is False

    def test_delete_requires_basic_auth(self, client):
        r = client.post("/license/delete", data={"install_id": "x"})
        assert r.status_code == 401

    def test_delete_web(self, client):
        _register(client)
        assert _get_license(client).get_json()["license"] is not None
        r = client.post(
            "/license/delete",
            data={"install_id": "test-install-1"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        assert r.get_json()["deleted"] is True
        with _connect() as conn:
            ins = conn.execute(
                "SELECT COUNT(*) AS n FROM installs WHERE install_id = ?",
                ("test-install-1",),
            ).fetchone()["n"]
            lic = conn.execute(
                "SELECT COUNT(*) AS n FROM licenses WHERE install_id = ?",
                ("test-install-1",),
            ).fetchone()["n"]
        assert ins == 0 and lic == 0

    def test_delete_unknown_install(self, client):
        r = client.post(
            "/license/delete",
            data={"install_id": "ghost"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 404
        assert r.get_json()["status"] == "error"

    def test_delete_missing_install_id(self, client):
        r = client.post("/license/delete", data={}, auth=("admin", "test-secret"))
        assert r.status_code == 400

    def test_cli_install_delete(self, client):
        _register(client)
        r = _run_cli("install:delete", "--install", "test-install-1")
        assert r.returncode == 0
        assert "Deleted install test-install-1" in r.stdout
        with _connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM installs WHERE install_id = ?",
                ("test-install-1",),
            ).fetchone()["n"]
        assert n == 0

    def test_cli_install_delete_unknown(self, client):
        r = _run_cli("install:delete", "--install", "ghost")
        assert r.returncode == 1
        assert "Install not found" in r.stdout
