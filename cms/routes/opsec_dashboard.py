import logging
import threading
import time

from flask import render_template, jsonify
from flask_login import login_required

from . import cms_bp
from ..auth import admin_required
from ..opsec_check import run_opsec_checks
from ..services.audit_chain import get_chain_status, verify_chain
from ..services.http_utils import is_tor_enabled, get_tor_proxy
from ..services.identity_isolation import is_identity_isolation_enabled
from ..models import Setting

logger = logging.getLogger(__name__)

_opsec_cache: dict[str, tuple[float, dict]] = {}
_opsec_cache_lock = threading.Lock()
_OPSEC_CACHE_TTL = 120


def _get_cached_opsec_checks() -> dict:
    now = time.time()
    with _opsec_cache_lock:
        cached = _opsec_cache.get("results")
        if cached and (now - cached[0]) < _OPSEC_CACHE_TTL:
            return cached[1]
    fresh = run_opsec_checks(verbose=False)
    with _opsec_cache_lock:
        _opsec_cache["results"] = (time.time(), fresh)
    return fresh


_OPSEC_KEYS = [
    "jitter_enabled",
    "jitter_min",
    "jitter_max",
    "proxy_rotation_enabled",
    "proxy_list",
    "impersonate_rotation_enabled",
    "impersonate_profiles",
    "domain_impersonation_enabled",
    "playwright_fallback_enabled",
    "playwright_stealth_enabled",
    "audit_chain_enabled",
    "identity_isolation_enabled",
    "tor_enabled",
    "tor_proxy",
    "tor_strict_mode",
]

_OPSEC_DEFAULTS: dict[str, str | None] = {
    "jitter_enabled": "true",
    "jitter_min": "0.3",
    "jitter_max": "2.0",
    "proxy_rotation_enabled": "false",
    "proxy_list": None,  # None = leeg/niet geconfigureerd → toont "—"
    "impersonate_rotation_enabled": "true",
    "impersonate_profiles": None,
    "domain_impersonation_enabled": "false",
    "playwright_fallback_enabled": "false",
    "playwright_stealth_enabled": "false",
    "audit_chain_enabled": "false",
    "identity_isolation_enabled": "false",
    "tor_enabled": "false",
    "tor_proxy": "socks5h://127.0.0.1:9050",
    "tor_strict_mode": "false",
}


@cms_bp.route("/admin/opsec-dashboard")
@login_required
@admin_required
def opsec_dashboard():
    """OPSEC Dashboard — status of all security layers."""
    checks = _get_cached_opsec_checks()
    settings_list = []
    for key in _OPSEC_KEYS:
        default = _OPSEC_DEFAULTS.get(key)
        settings_list.append({"key": key, "value": Setting.get(key, default)})
    return render_template(
        "cms/opsec_dashboard.html",
        checks=checks,
        settings_list=settings_list,
    )


@cms_bp.route("/api/opsec/status")
@login_required
@admin_required
def opsec_status_api():
    """JSON endpoint for live OPSEC status."""
    checks = run_opsec_checks(verbose=False)
    chain = get_chain_status()
    tor_health = (
        _get_cached_opsec_checks()["checks"]
        .get("tor_config", {})
        .get("detail", "unknown")
    )

    settings = {}
    for key in _OPSEC_KEYS:
        default = _OPSEC_DEFAULTS.get(key)
        settings[key] = Setting.get(key, default)

    return jsonify(
        {
            "checks": checks,
            "tor": {
                "enabled": is_tor_enabled(),
                "proxy": get_tor_proxy(),
                "health": tor_health,
            },
            "audit_chain": {
                "enabled": Setting.get("audit_chain_enabled", "false") == "true",
                "length": chain.get("length", 0),
                "last_hash": chain.get("last_hash"),
            },
            "identity_isolation": {
                "enabled": is_identity_isolation_enabled(),
            },
            "settings": settings,
        }
    )


@cms_bp.route("/api/opsec/verify-chain")
@login_required
@admin_required
def opsec_verify_chain():
    """Verify the OSINT audit hash chain integrity."""
    result = verify_chain()
    return jsonify(result)


@cms_bp.route("/api/opsec/run-checks", methods=["POST"])
@login_required
@admin_required
def opsec_run_checks():
    """Force-run all OPSEC checks (invalidates cache)."""
    with _opsec_cache_lock:
        _opsec_cache.pop("results", None)
    checks = _get_cached_opsec_checks()
    return jsonify(checks)
