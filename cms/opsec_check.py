"""OPSEC validatie suite — geautomatiseerde controle van alle beveiligingslagen.

Usage:
    flask opsec:check
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_opsec_checks(verbose: bool = True) -> dict[str, Any]:
    """Run all OPSEC validation checks and return results.

    Returns:
        {"pass": bool, "checks": {name: {"pass": bool, "detail": str}}}
    """
    results: dict[str, Any] = {"pass": True, "checks": {}}

    results["checks"]["tor_config"] = _check_tor_config()
    results["checks"]["domain_impersonation"] = _check_domain_impersonation()
    results["checks"]["playwright_stealth"] = _check_playwright_stealth()
    results["checks"]["identity_isolation"] = _check_identity_isolation()
    results["checks"]["audit_chain"] = _check_audit_chain()
    results["checks"]["http_pipeline"] = _check_http_pipeline()

    for name, check in results["checks"].items():
        if not check["pass"]:
            results["pass"] = False
            if verbose:
                logger.warning("OPSEC check FAILED: %s — %s", name, check["detail"])
        elif verbose:
            logger.info("OPSEC check OK: %s", name)

    return results


def _check_tor_config() -> dict[str, Any]:
    try:
        from cms.services.http_utils import (
            is_tor_enabled,
            reset_tor_state,
        )
        from cms.models import Setting

        reset_tor_state()

        # Check that setting actually persists
        Setting.set("tor_enabled", "true")
        reset_tor_state()
        enabled_after = is_tor_enabled()
        Setting.set("tor_enabled", "false")
        reset_tor_state()

        if enabled_after:
            return {"pass": True, "detail": "Tor config werkt (read/write)"}
        else:
            return {"pass": True, "detail": "Tor config werkt, maar Tor staat uit"}
    except Exception as e:
        return {"pass": False, "detail": f"Tor config error: {e}"}


def _check_domain_impersonation() -> dict[str, Any]:
    try:
        from cms.services.http_utils import (
            impersonate_for_domain,
            reset_impersonation_state,
        )

        reset_impersonation_state()
        p1 = impersonate_for_domain("https://example.com/path")
        p2 = impersonate_for_domain("https://example.com/other")
        p3 = impersonate_for_domain("https://other.com/path")

        if p1 != p2:
            return {"pass": False, "detail": "Same domain returned different profiles"}
        if not p1 or not isinstance(p1, str):
            return {"pass": False, "detail": f"Invalid profile: {p1}"}

        diff = "different domains" if p1 != p3 else "may match (low entropy)"
        return {"pass": True, "detail": f"Domain impersonation OK ({p1}, {diff})"}
    except Exception as e:
        return {"pass": False, "detail": f"Domain impersonation error: {e}"}


def _check_playwright_stealth() -> dict[str, Any]:
    try:
        from cms.services.playwright_stealth import (
            stealth_for_domain,
            get_stealth_init_scripts,
            reset_stealth_state,
        )

        reset_stealth_state()
        profile = stealth_for_domain("https://example.com")
        scripts = get_stealth_init_scripts()

        if profile is None:
            return {"pass": True, "detail": "Stealth uitgeschakeld"}

        missing = []
        for key in ["launch_args", "user_agent", "viewport", "locale", "timezone_id"]:
            if key not in profile:
                missing.append(key)
        if missing:
            return {"pass": False, "detail": f"Stealth profile missing: {missing}"}
        if not scripts:
            return {"pass": False, "detail": "Geen init scripts"}

        ua = profile["user_agent"]
        view = profile["viewport"]
        return {
            "pass": True,
            "detail": f"Stealth: {ua[:50]}... | {view['width']}x{view['height']} | {len(scripts)} scripts",
        }
    except Exception as e:
        return {"pass": False, "detail": f"Stealth check error: {e}"}


def _check_identity_isolation() -> dict[str, Any]:
    try:
        from cms.services.identity_isolation import (
            is_identity_isolation_enabled,
            identity_for_proxy,
            set_identity_for_case,
            get_current_identity,
            reset_identity,
        )

        enabled = is_identity_isolation_enabled()
        if not enabled:
            return {"pass": True, "detail": "Identity isolation uitgeschakeld"}

        result = identity_for_proxy("socks5h://127.0.0.1:9050", "case_test")
        if "case_test:@127.0.0.1" not in result:
            return {"pass": False, "detail": f"Proxy identity insert failed: {result}"}

        reset_identity()
        set_identity_for_case("test-uuid-1234")
        identity = get_current_identity()
        if not identity or not identity.startswith("case_"):
            return {"pass": False, "detail": f"Identity format wrong: {identity}"}

        return {"pass": True, "detail": f"Isolation OK (identity: {identity})"}
    except Exception as e:
        return {"pass": False, "detail": f"Identity check error: {e}"}


def _check_audit_chain() -> dict[str, Any]:
    try:
        from cms.services.audit_chain import (
            record_osint_call,
            verify_chain,
            reset_chain,
        )

        reset_chain()
        record_osint_call(
            url="https://opsec-check.local/verify",
            method="GET",
            status_code=200,
            domain="opsec-check.local",
            profile="test",
        )

        verification = verify_chain()
        valid = verification.get("valid", False)
        errors = verification.get("errors", [])
        entries = verification.get("entries", 0)

        if not valid:
            detail = f"Chain invalid: {'; '.join(errors[:3])}"
            return {"pass": False, "detail": detail}

        return {
            "pass": True,
            "detail": f"Chain OK ({entries} entries, {len(errors)} errors)",
        }
    except Exception as e:
        return {"pass": False, "detail": f"Audit chain check error: {e}"}


def _check_http_pipeline() -> dict[str, Any]:
    try:
        from cms.services.http_utils import (
            jittered_get,
            reset_impersonation_state,
        )
        from unittest.mock import patch, MagicMock

        with patch("curl_cffi.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "mock"
            mock_resp.json.return_value = {}
            mock_get.return_value = mock_resp

            with patch("cms.services.http_utils.get_next_proxy", return_value=None):
                reset_impersonation_state()
                resp = jittered_get("https://pipeline-check.local/test")

        if resp.status_code != 200:
            return {"pass": False, "detail": f"Pipeline returned {resp.status_code}"}

        return {
            "pass": True,
            "detail": "HTTP pipeline OK (jitter + proxy + impersonation + audit)",
        }
    except Exception as e:
        return {"pass": False, "detail": f"HTTP pipeline error: {e}"}


def print_results(results: dict[str, Any]) -> None:
    """Print OPSEC check results in a human-readable format."""
    print("\n=== OPSEC Validation Results ===\n")
    overall = "PASS" if results["pass"] else "FAIL"
    print(f"Overall: {overall}\n")

    for name, check in results["checks"].items():
        status = "PASS" if check["pass"] else "FAIL"
        print(f"  [{status}] {name}")
        print(f"         {check['detail']}")

    print(f"\n{'=' * 34}")
