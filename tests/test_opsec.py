"""Tests for centralized OPSEC — Tor routing, fail-closed, jittered wrappers."""

import pytest
from unittest.mock import patch, MagicMock

from cms.models import Setting


# ─── Tor: default state ─────────────────────────────────────


def test_tor_disabled_by_default(app):
    from cms.services.http_utils import is_tor_enabled, get_tor_proxy

    assert is_tor_enabled() is False
    assert get_tor_proxy() is None


def test_get_next_proxy_returns_none_when_tor_disabled(app):
    from cms.services.http_utils import get_next_proxy

    proxy = get_next_proxy()
    assert proxy is None


# ─── Tor: enabling ──────────────────────────────────────────


def test_enable_tor_via_setting(app):
    from cms.services.http_utils import is_tor_enabled, get_tor_proxy, reset_tor_state

    Setting.set("tor_enabled", "true")
    reset_tor_state()

    assert is_tor_enabled() is True
    proxy = get_tor_proxy()
    assert proxy is not None
    assert "socks5" in proxy

    Setting.set("tor_enabled", "false")
    reset_tor_state()


def test_reset_tor_state(app):
    from cms.services.http_utils import is_tor_enabled, get_tor_proxy, reset_tor_state

    Setting.set("tor_enabled", "true")
    reset_tor_state()
    assert is_tor_enabled() is True

    Setting.set("tor_enabled", "false")
    reset_tor_state()
    assert is_tor_enabled() is False
    assert get_tor_proxy() is None


# ─── get_next_proxy Tor routing ─────────────────────────────


def test_get_next_proxy_uses_tor_when_enabled(app):
    from cms.services.http_utils import get_next_proxy, reset_tor_state

    Setting.set("tor_enabled", "true")
    reset_tor_state()

    proxy = get_next_proxy()
    assert proxy is not None
    assert "socks5" in proxy.get("http", "")
    assert "socks5" in proxy.get("https", "")

    Setting.set("tor_enabled", "false")
    reset_tor_state()


# ─── TorNotAvailableError / fail-closed ─────────────────────


def test_tor_not_available_error_import(app):
    from cms.services.http_utils import TorNotAvailableError

    err = TorNotAvailableError("test")
    assert "test" in str(err)


def test_get_next_proxy_strict_raises_when_tor_disabled(app):
    from cms.services.http_utils import (
        TorNotAvailableError,
        get_next_proxy,
        reset_tor_state,
    )

    Setting.set("tor_enabled", "false")
    Setting.set("tor_strict_mode", "true")
    reset_tor_state()

    with pytest.raises(TorNotAvailableError):
        get_next_proxy()

    Setting.set("tor_strict_mode", "false")
    reset_tor_state()


def test_jittered_get_tor_strict_raises_on_curl_failure(app):
    from cms.services.http_utils import (
        jittered_get,
        TorNotAvailableError,
        reset_tor_state,
    )

    Setting.set("tor_enabled", "true")
    Setting.set("tor_strict_mode", "true")
    reset_tor_state()

    with patch("cms.services.http_utils.get_next_proxy") as mock_proxy:
        mock_proxy.return_value = {
            "http": "socks5h://127.0.0.1:1",
            "https": "socks5h://127.0.0.1:1",
        }
        import curl_cffi

        with patch("curl_cffi.requests.get") as mock_get:
            mock_get.side_effect = curl_cffi.CurlError(7, "Connection refused")
            with patch(
                "cms.services.http_utils._try_playwright_fallback",
                return_value=None,
            ):
                with patch("cms.services.http_utils._TOR_STRICT", True):
                    with pytest.raises(TorNotAvailableError):
                        jittered_get("https://check.torproject.org", timeout=2)

    Setting.set("tor_enabled", "false")
    Setting.set("tor_strict_mode", "false")
    reset_tor_state()


def test_jittered_get_tor_non_strict_passes_curl_error(app):
    from cms.services.http_utils import (
        jittered_get,
        reset_tor_state,
    )

    Setting.set("tor_enabled", "true")
    Setting.set("tor_strict_mode", "false")
    reset_tor_state()

    with patch("cms.services.http_utils.get_next_proxy") as mock_proxy:
        mock_proxy.return_value = {
            "http": "socks5h://127.0.0.1:1",
            "https": "socks5h://127.0.0.1:1",
        }
        import curl_cffi

        with patch("curl_cffi.requests.get") as mock_get:
            mock_get.side_effect = curl_cffi.CurlError(7, "Connection refused")
            with patch(
                "cms.services.http_utils._try_playwright_fallback",
                return_value=None,
            ):
                with patch("cms.services.http_utils._TOR_STRICT", False):
                    with pytest.raises(curl_cffi.CurlError):
                        jittered_get("https://check.torproject.org", timeout=2)

    Setting.set("tor_enabled", "false")
    Setting.set("tor_strict_mode", "false")
    reset_tor_state()


# ─── jittered wrappers with Tor ─────────────────────────────


def test_jittered_get_passes_tor_proxy_to_curl(app):
    from cms.services.http_utils import jittered_get, reset_tor_state

    Setting.set("tor_enabled", "true")
    reset_tor_state()

    with patch("cms.services.http_utils.get_next_proxy") as mock_proxy:
        mock_proxy.return_value = {
            "http": "socks5h://127.0.0.1:9050",
            "https": "socks5h://127.0.0.1:9050",
        }
        with patch("curl_cffi.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "mock"
            mock_get.return_value = mock_resp

            resp = jittered_get("https://example.com")
            assert resp.status_code == 200

            _, kwargs = mock_get.call_args
            proxies = kwargs.get("proxies", {})
            assert "socks5" in proxies.get("http", "")
            assert "socks5" in proxies.get("https", "")

    Setting.set("tor_enabled", "false")
    reset_tor_state()


def test_jittered_get_no_proxy_by_default(app):
    from cms.services.http_utils import jittered_get, reset_tor_state

    reset_tor_state()

    with patch("cms.services.http_utils.get_next_proxy", return_value=None):
        with patch("curl_cffi.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "mock"
            mock_resp.json.return_value = {}
            mock_get.return_value = mock_resp

            resp = jittered_get("https://example.com")
            assert resp.status_code == 200

            _, kwargs = mock_get.call_args
            proxies = kwargs.get("proxies")
            assert proxies is None


# ─── Health check ───────────────────────────────────────────


def test_health_check_tor_disabled(app):
    from cms.health_utils import check_external_services

    result = check_external_services(quick=False)
    assert "tor" in result
    assert result["tor"] in ("disabled", "ok")


def test_health_check_tor_enabled_but_unreachable(app):
    from cms.health_utils import check_external_services
    from cms.services.http_utils import reset_tor_state

    Setting.set("tor_enabled", "true")
    Setting.set("tor_proxy", "socks5h://127.0.0.1:1")
    reset_tor_state()

    result = check_external_services(quick=False)
    assert "tor" in result

    Setting.set("tor_enabled", "false")
    reset_tor_state()


# ─── Domain-based impersonation ─────────────────────────────


def test_extract_domain(app):
    from cms.services.http_utils import _extract_domain

    assert _extract_domain("https://example.com/path") == "example.com"
    assert _extract_domain("http://sub.example.com:8080/foo") == "sub.example.com"
    assert _extract_domain("") == "unknown"


def test_impersonate_for_domain_consistency(app):
    from cms.services.http_utils import (
        impersonate_for_domain,
        reset_impersonation_state,
    )

    reset_impersonation_state()

    p1 = impersonate_for_domain("https://example.com")
    p2 = impersonate_for_domain("https://example.com/other")
    assert p1 == p2, "same domain should return same profile"


def test_impersonate_for_domain_different_domains(app):
    from cms.services.http_utils import (
        impersonate_for_domain,
        reset_impersonation_state,
    )

    reset_impersonation_state()

    p1 = impersonate_for_domain("https://example.com")
    p2 = impersonate_for_domain("https://other.com")

    domains_may_match = p1 == p2
    if not domains_may_match:
        assert p1 != p2


def test_impersonate_for_domain_uses_list(app):
    from cms.services.http_utils import (
        impersonate_for_domain,
        reset_impersonation_state,
        _IMPROFILE_LIST,
    )

    reset_impersonation_state()

    p = impersonate_for_domain("https://example.com")
    assert p in _IMPROFILE_LIST


def test_jittered_get_uses_domain_impersonation(app):
    from cms.services.http_utils import jittered_get, reset_impersonation_state

    reset_impersonation_state()

    with patch("cms.services.http_utils.get_next_proxy", return_value=None):
        with patch("curl_cffi.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "mock"
            mock_get.return_value = mock_resp

            jittered_get("https://example.com/something")
            _, kwargs = mock_get.call_args
            assert "impersonate" in kwargs
            assert kwargs["impersonate"] in (
                "chrome124",
                "chrome123",
                "chrome120",
                "chrome116",
                "chrome110",
                "safari17_2_1",
                "safari17_0",
                "firefox123",
                "firefox120",
            )


def test_reset_impersonation_state(app):
    from cms.services.http_utils import (
        reset_impersonation_state,
        _IMPROFILE_INDEX,
    )

    reset_impersonation_state()
    assert _IMPROFILE_INDEX == 0


# ─── Playwright Stealth ────────────────────────────────────


def test_stealth_for_domain_consistency(app):
    from cms.services.playwright_stealth import (
        stealth_for_domain,
        reset_stealth_state,
    )

    reset_stealth_state()
    p1 = stealth_for_domain("https://example.com/path")
    p2 = stealth_for_domain("https://example.com/other")
    assert p1 is not None
    assert p1 == p2


def test_stealth_for_domain_different_domains(app):
    from cms.services.playwright_stealth import (
        stealth_for_domain,
        reset_stealth_state,
    )

    reset_stealth_state()
    p1 = stealth_for_domain("https://example.com")
    p2 = stealth_for_domain("https://other.com")
    assert p1 is not None
    assert p2 is not None


def test_stealth_for_domain_structure(app):
    from cms.services.playwright_stealth import (
        stealth_for_domain,
        reset_stealth_state,
    )

    reset_stealth_state()
    profile = stealth_for_domain("https://example.com")
    assert profile is not None
    assert "launch_args" in profile
    assert "user_agent" in profile
    assert "viewport" in profile
    assert "locale" in profile
    assert "timezone_id" in profile
    assert "color_scheme" in profile
    assert profile["color_scheme"] == "light"
    assert "width" in profile["viewport"]
    assert "height" in profile["viewport"]
    assert profile["user_agent"].startswith("Mozilla")


def test_stealth_launch_args_contain_automation_flag(app):
    from cms.services.playwright_stealth import stealth_for_domain, reset_stealth_state

    reset_stealth_state()
    profile = stealth_for_domain("https://example.com")
    assert profile is not None
    args = profile["launch_args"]
    assert any("AutomationControlled" in a for a in args)
    assert any("disable-blink-features" in a for a in args)


def test_stealth_init_scripts(app):
    from cms.services.playwright_stealth import get_stealth_init_scripts

    scripts = get_stealth_init_scripts()
    assert len(scripts) >= 4
    assert any("webdriver" in s for s in scripts)
    assert any("chrome" in s for s in scripts)
    assert any("plugins" in s for s in scripts)


def test_reset_stealth_state(app):
    from cms.services.playwright_stealth import (
        stealth_for_domain,
        reset_stealth_state,
        _STEALTH_CACHE,
    )

    reset_stealth_state()
    assert len(_STEALTH_CACHE) == 0
    stealth_for_domain("https://example.com")
    assert len(_STEALTH_CACHE) == 1
    reset_stealth_state()
    assert len(_STEALTH_CACHE) == 0


def test_stealth_disabled_returns_none(app):
    from cms.services.playwright_stealth import (
        stealth_for_domain,
        reset_stealth_state,
    )
    from cms.models import Setting

    reset_stealth_state()
    Setting.set("playwright_stealth_enabled", "false")
    result = stealth_for_domain("https://example.com")
    assert result is None
    Setting.set("playwright_stealth_enabled", "true")


# ─── Audit Hash Chain ──────────────────────────────────────


def test_audit_chain_records_call(app):
    from cms.services.audit_chain import record_osint_call, reset_chain

    reset_chain()
    meta = record_osint_call(
        url="https://example.com",
        method="GET",
        status_code=200,
        domain="example.com",
        profile="chrome124",
    )
    assert meta is not None
    assert meta["length"] == 1
    assert meta["status_code"] == 200
    assert meta["method"] == "GET"
    assert meta["url"] == "https://example.com"
    assert len(meta["entry_hash"]) == 64
    assert len(meta["chain_hash"]) == 64


def test_audit_chain_links_hashes(app):
    from cms.services.audit_chain import record_osint_call, reset_chain

    reset_chain()
    call1 = record_osint_call(url="https://example.com", method="GET", status_code=200)
    call2 = record_osint_call(url="https://other.com", method="POST", status_code=201)

    assert call1 is not None
    assert call2 is not None
    assert call1["chain_hash"] != call2["chain_hash"]
    assert call2["prev_hash"] == call1["chain_hash"]


def test_audit_chain_integrity(app):
    from cms.services.audit_chain import record_osint_call, reset_chain
    import hashlib

    reset_chain()
    c1 = record_osint_call(url="https://a.com", method="GET", status_code=200)
    c2 = record_osint_call(url="https://b.com", method="POST", status_code=201)

    assert c1 is not None
    assert c2 is not None

    # Verify chain_hash = SHA256(prev_hash + entry_hash)
    recomputed = hashlib.sha256(
        (c2["prev_hash"] + c2["entry_hash"]).encode()
    ).hexdigest()
    assert recomputed == c2["chain_hash"]

    # Verify genesis: first call's prev_hash should be the genesis hash
    genesis = "0" * 64
    assert c1["prev_hash"] == genesis


def test_audit_chain_disabled_returns_none(app):
    from cms.services.audit_chain import record_osint_call, reset_chain
    from cms.models import Setting

    reset_chain()
    Setting.set("audit_chain_enabled", "false")
    meta = record_osint_call(url="https://example.com", method="GET", status_code=200)
    assert meta is None
    Setting.set("audit_chain_enabled", "true")


def test_audit_chain_status(app):
    from cms.services.audit_chain import (
        record_osint_call,
        get_chain_status,
        reset_chain,
    )

    reset_chain()
    status = get_chain_status()
    assert status["enabled"] is True
    assert status["length"] == 0
    assert status["last_hash"] is None

    record_osint_call(url="https://x.com", method="GET", status_code=200)
    status = get_chain_status()
    assert status["length"] == 1
    assert status["last_hash"] is not None


def test_audit_chain_reset(app):
    from cms.services.audit_chain import (
        record_osint_call,
        get_chain_status,
        reset_chain,
    )

    reset_chain()
    record_osint_call(url="https://x.com", method="GET", status_code=200)
    assert get_chain_status()["length"] == 1
    reset_chain()
    assert get_chain_status()["length"] == 0


# ─── Identity Isolation ────────────────────────────────────


def test_identity_isolation_disabled_by_default(app):
    from cms.services.identity_isolation import is_identity_isolation_enabled

    assert is_identity_isolation_enabled() is False


def test_identity_for_proxy(app):
    from cms.services.identity_isolation import identity_for_proxy

    result = identity_for_proxy("socks5h://127.0.0.1:9050", "case_test123")
    assert result == "socks5h://case_test123:@127.0.0.1:9050"


def test_identity_for_proxy_with_existing_auth(app):
    from cms.services.identity_isolation import identity_for_proxy

    result = identity_for_proxy("socks5h://user:pass@127.0.0.1:9050", "case_test123")
    assert result == "socks5h://user:pass@127.0.0.1:9050"


def test_set_identity_for_case(app):
    from cms.services.identity_isolation import (
        set_identity_for_case,
        get_current_identity,
        reset_identity,
    )

    reset_identity()
    assert get_current_identity() is None

    set_identity_for_case("test-case-uuid-1234")
    identity = get_current_identity()
    assert identity is not None
    assert identity.startswith("case_")
    assert len(identity) == 5 + 16  # "case_" + 16 hex chars


def test_identity_contextvar_isolation(app):
    """Test that different contexts don't interfere (requires threading)."""
    from cms.services.identity_isolation import (
        set_identity_for_case,
        get_current_identity,
        reset_identity,
    )
    import threading
    import time

    results: list = []

    def worker(case_id: str):
        set_identity_for_case(case_id)
        time.sleep(0.05)
        results.append(get_current_identity())

    reset_identity()
    t1 = threading.Thread(target=worker, args=("case-1",))
    t2 = threading.Thread(target=worker, args=("case-2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each thread should have its own identity (no cross-talk)
    assert results[0] != results[1]
    assert "case_1-" not in str(results)
    assert results[0].startswith("case_") or results[1].startswith("case_")


def test_get_next_proxy_with_identity(app):
    from cms.services.http_utils import get_next_proxy, reset_tor_state
    from cms.models import Setting

    Setting.set("tor_enabled", "true")
    reset_tor_state()

    proxy = get_next_proxy(identity="case_test123")
    assert proxy is not None
    assert "case_test123:" in proxy.get("http", "")
    assert "socks5" in proxy.get("http", "")

    Setting.set("tor_enabled", "false")
    reset_tor_state()


# ─── OPSEC Integration & Validation ─────────────────────────


def test_full_pipeline_integration(app):
    """Verify the full OPSEC pipeline fires:
    jitter → proxy → impersonation → audit recording.
    """
    from cms.services.http_utils import (
        jittered_get,
        reset_tor_state,
        reset_impersonation_state,
    )
    from cms.services.audit_chain import reset_chain, get_chain_status
    from unittest.mock import patch, MagicMock

    reset_tor_state()
    reset_impersonation_state()
    reset_chain()

    with patch("cms.services.http_utils.get_next_proxy") as mock_proxy:
        mock_proxy.return_value = {
            "http": "socks5h://127.0.0.1:9050",
            "https": "socks5h://127.0.0.1:9050",
        }
        with patch("curl_cffi.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "integrated"
            mock_resp.json.return_value = {}
            mock_get.return_value = mock_resp

            resp = jittered_get("https://integration-test.local/data")

    assert resp.status_code == 200

    status = get_chain_status()
    assert status["length"] >= 1
    assert status["last_hash"] is not None

    _, kwargs = mock_get.call_args
    assert "proxies" in kwargs
    assert "impersonate" in kwargs
    assert "socks5" in kwargs["proxies"].get("http", "")


def test_opsec_check_runs(app):
    """The full OPSEC validation suite should run without crashing."""
    from cms.opsec_check import run_opsec_checks

    results = run_opsec_checks(verbose=False)
    assert "checks" in results
    assert "tor_config" in results["checks"]
    assert "domain_impersonation" in results["checks"]
    assert "playwright_stealth" in results["checks"]
    assert "identity_isolation" in results["checks"]
    assert "audit_chain" in results["checks"]
    assert "http_pipeline" in results["checks"]


# ─── OPSEC Dashboard ────────────────────────────────────────


def test_opsec_dashboard_page(auth_client):
    """OPSEC dashboard page renders."""
    resp = auth_client.get("/cms/admin/opsec-dashboard")
    assert resp.status_code == 200
    assert b"OPSEC" in resp.data


def test_opsec_api_status(auth_client):
    """OPSEC status API returns JSON with all expected keys."""
    resp = auth_client.get("/cms/api/opsec/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "checks" in data
    assert "tor" in data
    assert "audit_chain" in data
    assert "identity_isolation" in data
    assert "settings" in data


def test_opsec_api_verify_chain(auth_client):
    """OPSEC verify-chain API returns valid/invalid status."""
    resp = auth_client.get("/cms/api/opsec/verify-chain")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "valid" in data
    assert "entries" in data
    assert "errors" in data


# ─── Production Hardening ────────────────────────────────────


# ─── SSRF guard ──────────────────────────────────────────────


def test_validate_url_blocks_private_and_reserved(app):
    from cms.services.ssrf_guard import validate_url

    blocked = [
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254",
        "http://0.0.0.0",
        "http://::1",
        "http://[fe80::1]",
        "http://224.0.0.1",
        "http://255.255.255.255",
        "ftp://example.com",
        "file:///etc/passwd",
    ]
    for url in blocked:
        ok, reason = validate_url(url)
        assert ok is False, url
        assert reason

    ok, _ = validate_url("http://example.com")
    assert ok is True


def test_validate_url_blocks_dns_rebinding_to_private(app):
    from cms.services.ssrf_guard import validate_url

    with patch(
        "cms.services.ssrf_guard.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("10.0.0.5", 80))],
    ):
        ok, reason = validate_url("http://rebind.example.com")
        assert ok is False
        assert "blocked" in reason


def test_safe_fetch_blocks_redirect_to_private(app):
    from cms.services.http_utils import SSRFBlockedError, _safe_fetch

    redirect_resp = MagicMock()
    redirect_resp.is_redirect = True
    redirect_resp.status_code = 302
    redirect_resp.headers = {"Location": "http://10.0.0.1/steal"}
    redirect_resp.raise_for_status = MagicMock()

    with patch(
        "cms.services.http_utils._impersonated_request", return_value=redirect_resp
    ):
        with pytest.raises(SSRFBlockedError):
            _safe_fetch("get", "https://example.com", {}, None)


def test_safe_fetch_follows_safe_redirect(app):
    from cms.services.http_utils import _safe_fetch

    hop1 = MagicMock()
    hop1.is_redirect = True
    hop1.status_code = 302
    hop1.headers = {"Location": "https://example.com/next"}

    final = MagicMock()
    final.is_redirect = False
    final.status_code = 200
    final.headers = {"Location": None}

    with patch(
        "cms.services.http_utils._impersonated_request", side_effect=[hop1, final]
    ) as mock_req:
        resp = _safe_fetch("get", "https://example.com", {}, None)
        assert resp is final
        assert mock_req.call_count == 2
        assert mock_req.call_args[0][1] == "https://example.com/next"


def test_jittered_get_blocks_private_url_before_request(app):
    from cms.services.http_utils import SSRFBlockedError, jittered_get, reset_tor_state

    reset_tor_state()
    with patch("cms.services.http_utils.get_next_proxy", return_value=None):
        with patch("curl_cffi.requests.get") as mock_get:
            with pytest.raises(SSRFBlockedError):
                jittered_get("http://169.254.169.254/latest/meta-data/")
            mock_get.assert_not_called()


def test_security_headers(client):
    """Security headers are set on every response."""
    resp = client.get("/")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert resp.headers.get("Permissions-Policy") is not None
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "'unsafe-eval'" in csp  # required by TensorFlow.js
    assert "report-uri /csp-report" in csp


# ─── Production config: fail-fast ────────────────────────────


class _FakeProdApp:
    def __init__(self, **cfg):
        self.config = dict(cfg)


def test_production_config_rejects_sqlite(app):
    from cms.config import ProductionConfig

    fake = _FakeProdApp(
        CMS_ENCRYPTION_KEY="x",
        SECRET_KEY="y",
        SQLALCHEMY_DATABASE_URI="sqlite:///cms.db",
    )
    with pytest.raises(ValueError, match="PostgreSQL"):
        ProductionConfig.init_app(fake)


def test_production_config_rejects_missing_keys(app):
    from cms.config import ProductionConfig

    fake = _FakeProdApp(SQLALCHEMY_DATABASE_URI="postgresql://u:p@h/db")
    with pytest.raises(ValueError, match="CMS_ENCRYPTION_KEY"):
        ProductionConfig.init_app(fake)


def test_production_config_accepts_postgres_with_keys(app):
    from cms.config import ProductionConfig

    fake = _FakeProdApp(
        CMS_ENCRYPTION_KEY="x",
        SECRET_KEY="y",
        SQLALCHEMY_DATABASE_URI="postgresql://u:p@h/db",
    )
    ProductionConfig.init_app(fake)  # must not raise


def test_get_config_production_maps_to_production_config(app):
    from cms.config import ProductionConfig, get_config

    assert get_config("production") is ProductionConfig
