"""Tests for the Iveras license server (license-server/app.py + licensing.py).

These live outside the dashboard `tests/` dir so the root conftest.py (which
boots the full dashboard app) does not interfere. The license-server's own
`app.py` is loaded via importlib under a unique name to avoid colliding with
the dashboard's `app` module, and the CLI is exercised as a subprocess.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from datetime import timedelta

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tmp_dir = tempfile.mkdtemp(prefix="license-server-test-")
os.environ["LICENSE_DB_PATH"] = os.path.join(_tmp_dir, "license.db")
os.environ["LICENSE_KEY_PATH"] = os.path.join(_tmp_dir, "private.pem")
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-secret"
os.environ["LICENSE_ENV"] = "development"  # relax LICENSE_ADMIN_SECRET check in tests

sys.path.insert(0, _SERVER_DIR)

import licensing  # noqa: E402
import ipintel  # noqa: E402

# Keep a reference to the real enrich(); the autouse _no_network fixture replaces it.
_real_enrich = ipintel.enrich

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
        conn.execute("DELETE FROM ip_intel")
    yield


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Never hit external IP services during tests — mock the enrich entrypoint."""
    monkeypatch.setattr(
        ipintel,
        "enrich",
        lambda conn, ip: {
            "country": "Netherlands",
            "countryCode": "NL",
            "city": "Amsterdam",
            "isp": "Test-ISP",
            "as": "AS1234",
            "asname": "Test-AS",
            "hosting": True,
            "source": "mock",
        },
    )
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


def _get_csrf_token(client):
    r = client.get("/", auth=("admin", "test-secret"))
    assert r.status_code == 200
    m = re.search(r'var CSRF_TOKEN = "([^"]+)"', r.get_data(as_text=True))
    assert m, "CSRF token not found in dashboard"
    return m.group(1)


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


class TestClientIPTrustBoundary:
    def test_untrusted_peer_cannot_spoof_xff(self, client):
        r = client.post(
            "/api/register",
            json={
                "install_id": "spoofed-xff",
                "info": {"public_ip": "198.51.100.10"},
            },
            headers={
                "Authorization": "Bearer tok",
                "X-Forwarded-For": "8.8.8.8, 198.51.100.10",
            },
            environ_base={"REMOTE_ADDR": "198.51.100.10"},
        )
        assert r.status_code == 200
        with _connect() as conn:
            row = conn.execute(
                "SELECT last_ip, ip_check FROM installs WHERE install_id = ?",
                ("spoofed-xff",),
            ).fetchone()
        assert row["last_ip"] == "198.51.100.10"
        assert json.loads(row["ip_check"])["flag"] == "ok"

    def test_trusted_proxy_xff_is_used(self, client):
        r = client.post(
            "/api/register",
            json={"install_id": "trusted-proxy", "info": {"public_ip": "8.8.8.8"}},
            headers={
                "Authorization": "Bearer tok",
                "X-Forwarded-For": "8.8.8.8",
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert r.status_code == 200
        with _connect() as conn:
            row = conn.execute(
                "SELECT last_ip, ip_check FROM installs WHERE install_id = ?",
                ("trusted-proxy",),
            ).fetchone()
        assert row["last_ip"] == "8.8.8.8"
        assert json.loads(row["ip_check"])["flag"] == "ok"

    def test_nginx_overwrites_forwarded_for(self):
        with open(
            os.path.join(_SERVER_DIR, "deploy", "nginx.conf"), encoding="utf-8"
        ) as config:
            text = config.read()
        assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
        assert "$proxy_add_x_forwarded_for" not in text


class TestProductionSecret:
    def _import_app(self, env):
        return subprocess.run(
            [sys.executable, "-c", "import app"],
            capture_output=True,
            text=True,
            env=env,
            cwd=_SERVER_DIR,
        )

    def test_production_requires_admin_secret(self):
        env = {k: v for k, v in os.environ.items() if k != "LICENSE_ADMIN_SECRET"}
        env["LICENSE_ENV"] = "production"
        r = self._import_app(env)
        assert r.returncode != 0
        assert "LICENSE_ADMIN_SECRET" in r.stderr

    def test_production_with_secret_boots(self):
        env = dict(os.environ)
        env["LICENSE_ENV"] = "production"
        env["LICENSE_ADMIN_SECRET"] = "x" * 64
        r = self._import_app(env)
        assert r.returncode == 0, r.stderr


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
            headers={"X-CSRF-Token": _get_csrf_token(client)},
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

    def test_issue_missing_csrf(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "full", "days": "365"},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 403

    def test_issue_replaces_trial(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "trial", "days": "30"},
            headers={"X-CSRF-Token": _get_csrf_token(client)},
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
            headers={"X-CSRF-Token": _get_csrf_token(client)},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 200
        assert r.get_json()["license"]["expires_at"] == "2027-01-15T00:00:00Z"

    def test_issue_bad_plan(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "bogus", "days": "365"},
            headers={"X-CSRF-Token": _get_csrf_token(client)},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 400

    def test_issue_bad_days(self, client):
        _register(client)
        r = client.post(
            "/license/issue",
            data={"install_id": "test-install-1", "plan": "full", "days": "abc"},
            headers={"X-CSRF-Token": _get_csrf_token(client)},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 400

    def test_issue_unregistered_install(self, client):
        r = client.post(
            "/license/issue",
            data={"install_id": "ghost", "plan": "full", "days": "365"},
            headers={"X-CSRF-Token": _get_csrf_token(client)},
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
            headers={"X-CSRF-Token": _get_csrf_token(client)},
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
            headers={"X-CSRF-Token": _get_csrf_token(client)},
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
            headers={"X-CSRF-Token": _get_csrf_token(client)},
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
            headers={"X-CSRF-Token": _get_csrf_token(client)},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 404
        assert r.get_json()["status"] == "error"

    def test_delete_missing_install_id(self, client):
        r = client.post(
            "/license/delete",
            data={},
            headers={"X-CSRF-Token": _get_csrf_token(client)},
            auth=("admin", "test-secret"),
        )
        assert r.status_code == 400

    def test_dashboard_locked_without_password(self, client, monkeypatch):
        monkeypatch.setattr(ls_app, "ADMIN_PASSWORD", "")
        r = client.get("/")
        assert r.status_code == 401
        r2 = client.post(
            "/license/revoke", data={"install_id": "x"}, auth=("admin", "whatever")
        )
        assert r2.status_code == 401

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

    # ------------------------------------------------------------------
    # IP intelligence (ipintel)
    # ------------------------------------------------------------------

    def test_register_stores_ip_intel_and_http(self, client):
        _register(client)
        with _connect() as conn:
            row = conn.execute(
                "SELECT ip_intel, last_http FROM installs WHERE install_id = ?",
                ("test-install-1",),
            ).fetchone()
        intel = json.loads(row["ip_intel"])
        assert intel["countryCode"] == "NL"
        assert intel["hosting"] is True
        assert intel["source"] == "mock"
        http = json.loads(row["last_http"])
        assert http["ua"] and isinstance(http["ua"], str)
        assert http["protocol"] in ("HTTP/1.1", "HTTP/1.0")

    def test_ipintel_privacy_defaults_disable_external_sources(self):
        assert ipintel.GEO_SOURCE == "off"
        assert ipintel.PTR_ENABLED is False
        assert ipintel.RDAP_ENABLED is False

    def test_ipintel_privacy_default_makes_no_external_calls(self, monkeypatch):
        monkeypatch.setattr(
            ipintel, "_lookup_ptr", lambda ip: pytest.fail("PTR must be opt-in")
        )
        monkeypatch.setattr(
            ipintel, "_lookup_rdap", lambda ip: pytest.fail("RDAP must be opt-in")
        )
        monkeypatch.setattr(
            ipintel, "_lookup_ipapi", lambda ip: pytest.fail("ip-api must be opt-in")
        )
        with _connect() as conn:
            data = _real_enrich(conn, "8.8.8.8")
        assert data["disabled"] is True

    def test_ipapi_is_explicit_opt_in(self, monkeypatch):
        monkeypatch.setattr(ipintel, "GEO_SOURCE", "ip-api")
        monkeypatch.setattr(
            ipintel,
            "_http_json",
            lambda url: {"status": "success", "countryCode": "NL"},
        )
        assert ipintel._lookup_ipapi("8.8.8.8")["countryCode"] == "NL"

    def test_purge_removes_expired_ip_data_from_active_install(self):
        old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with _connect() as conn:
            conn.execute(
                "INSERT INTO installs "
                "(install_id, token_hash, last_seen, ip_intel, ip_intel_at, "
                "last_http, last_http_at, ip_check, ip_check_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "old",
                    "hash",
                    now,
                    '{"x":1}',
                    old,
                    '{"ua":"x"}',
                    old,
                    '{"flag":"ok"}',
                    old,
                ),
            )
            conn.execute(
                "INSERT INTO ip_intel (ip, data, queried_at, ttl_seconds) VALUES (?, ?, ?, ?)",
                ("8.8.8.8", "{}", 1, 1),
            )
            counts = ls_app.purge_sensitive_data(conn)
            row = conn.execute(
                "SELECT ip_intel, last_http, ip_check FROM installs WHERE install_id = 'old'"
            ).fetchone()
            cache_count = conn.execute("SELECT COUNT(*) AS n FROM ip_intel").fetchone()[
                "n"
            ]
        assert counts["ip_intel"] == 1
        assert row["ip_intel"] is None
        assert row["last_http"] is None
        assert row["ip_check"] is None
        assert cache_count == 0

    def test_cli_privacy_purge(self):
        result = _run_cli("privacy:purge")
        assert result.returncode == 0, result.stderr
        assert "Privacy purge completed" in result.stdout

    def test_ip_intelligence_export_is_audited(self, client):
        _register(client)
        r = client.get("/api/installs", auth=("admin", "test-secret"))
        assert r.status_code == 200
        with _connect() as conn:
            audit = conn.execute(
                "SELECT action, resource FROM admin_audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert (audit["action"], audit["resource"]) == ("export", "installs")

    def test_api_installs_exposes_ip_intel(self, client):
        r = _register(client)
        assert r.status_code == 200
        r2 = client.get("/api/installs", auth=("admin", "test-secret"))
        assert r2.status_code == 200
        body = r2.get_json()
        item = next(i for i in body["installs"] if i["install_id"] == "test-install-1")
        assert item["last_ip"] == "127.0.0.1"
        assert item["ip_intel"]["countryCode"] == "NL"
        assert item["last_http"]["ua"]

    def test_register_intel_failure_does_not_break_license(self, client, monkeypatch):
        def _boom(conn, ip):
            raise RuntimeError("geo service down")

        monkeypatch.setattr(ipintel, "enrich", _boom)
        r = _register(client)
        assert r.status_code == 200
        assert r.get_json()["registered"] is True
        lic = _get_license(client).get_json()["license"]
        assert lic is not None and lic["status"] == "active"

    def test_ipintel_private_skips_external_lookup(self, monkeypatch):
        monkeypatch.setattr(ipintel, "enrich", _real_enrich)
        monkeypatch.setattr(
            ipintel,
            "_lookup_ptr",
            lambda ip: pytest.fail("PTR must not run for private IP"),
        )
        monkeypatch.setattr(
            ipintel,
            "_lookup_rdap",
            lambda ip: pytest.fail("RDAP must not run for private IP"),
        )
        monkeypatch.setattr(
            ipintel,
            "_lookup_ipapi",
            lambda ip: pytest.fail("ip-api must not run for private IP"),
        )
        with _connect() as conn:
            data = _real_enrich(conn, "192.168.1.50")
        assert data["private"] is True

    def test_ipintel_caches_per_ip(self, monkeypatch):
        monkeypatch.setattr(ipintel, "enrich", _real_enrich)
        monkeypatch.setattr(ipintel, "PTR_ENABLED", True)
        monkeypatch.setattr(ipintel, "RDAP_ENABLED", True)
        monkeypatch.setattr(ipintel, "GEO_SOURCE", "ip-api")
        calls = {"n": 0}

        def fake_ptr(ip):
            calls["n"] += 1
            return "host.example.test"

        monkeypatch.setattr(ipintel, "_lookup_ptr", fake_ptr)
        monkeypatch.setattr(
            ipintel, "_lookup_rdap", lambda ip: {"netname": "EXAMPLE-NET"}
        )
        monkeypatch.setattr(
            ipintel,
            "_lookup_ipapi",
            lambda ip: {"countryCode": "NL", "source": "ip-api"},
        )
        with _connect() as conn:
            first = _real_enrich(conn, "8.8.8.8")
            second = _real_enrich(conn, "8.8.8.8")
        assert calls["n"] == 1
        assert first == second
        assert first["ptr"] == "host.example.test"

    def test_ipintel_failure_caches_negative(self, monkeypatch):
        monkeypatch.setattr(ipintel, "enrich", _real_enrich)
        monkeypatch.setattr(ipintel, "PTR_ENABLED", True)
        monkeypatch.setattr(ipintel, "RDAP_ENABLED", True)
        monkeypatch.setattr(ipintel, "GEO_SOURCE", "ip-api")
        monkeypatch.setattr(ipintel, "_lookup_ptr", lambda ip: None)
        monkeypatch.setattr(ipintel, "_lookup_rdap", lambda ip: {})
        monkeypatch.setattr(ipintel, "_lookup_ipapi", lambda ip: {})
        with _connect() as conn:
            first = _real_enrich(conn, "9.9.9.9")
            second = _real_enrich(conn, "9.9.9.9")
        assert "error" in first
        assert first == second

    # ------------------------------------------------------------------
    # IP cross-check (reported public_ip vs actually observed)
    # ------------------------------------------------------------------

    def _register_with_public_ip(self, client, public_ip):
        return client.post(
            "/api/register",
            json={
                "install_id": "ipcheck",
                "info": {"hostname": "host-1", "public_ip": public_ip},
            },
            headers={"Authorization": "Bearer tok"},
        )

    def _stored_ip_check(self, install_id="ipcheck"):
        with _connect() as conn:
            row = conn.execute(
                "SELECT ip_check FROM installs WHERE install_id = ?", (install_id,)
            ).fetchone()
        return json.loads(row["ip_check"])

    def test_ip_check_matching_reported(self, client):
        r = self._register_with_public_ip(client, "127.0.0.1")
        assert r.status_code == 200
        assert self._stored_ip_check()["flag"] == "ok"

    def test_ip_check_mismatch_public(self, client):
        r = self._register_with_public_ip(client, "8.8.8.8")
        assert r.status_code == 200
        check = self._stored_ip_check()
        assert check["flag"] == "mismatch"
        assert check["reported"] == "8.8.8.8"
        assert check["actual"] == "127.0.0.1"

    def test_ip_check_nat_private_reported(self, client):
        r = self._register_with_public_ip(client, "192.168.1.5")
        assert r.status_code == 200
        assert self._stored_ip_check()["flag"] == "nat"

    def test_ip_check_none_without_report(self, client):
        _register(client)  # info has no public_ip
        with _connect() as conn:
            row = conn.execute(
                "SELECT ip_check FROM installs WHERE install_id = 'test-install-1'"
            ).fetchone()
        assert json.loads(row["ip_check"])["flag"] == "none"

    def test_api_installs_exposes_ip_check(self, client):
        self._register_with_public_ip(client, "8.8.8.8")
        r = client.get("/api/installs", auth=("admin", "test-secret"))
        item = next(i for i in r.get_json()["installs"] if i["install_id"] == "ipcheck")
        assert item["ip_check"]["flag"] == "mismatch"
        assert item["public_ip"] == "8.8.8.8"
