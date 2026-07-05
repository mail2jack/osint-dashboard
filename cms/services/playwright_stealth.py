"""Playwright stealth configuration — evita headless detection per domain."""

import hashlib
import logging
import os
import threading
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_STEALTH_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

_STEALTH_VIEWPORTS: list[dict[str, int]] = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 1280, "height": 720},
]

_STEALTH_LOCALES: list[str] = [
    "en-US",
    "en-GB",
    "nl-NL",
    "de-DE",
    "fr-FR",
]

_STEALTH_TIMEZONES: list[str] = [
    "America/New_York",
    "Europe/London",
    "Europe/Amsterdam",
    "Europe/Berlin",
    "Europe/Paris",
    "America/Chicago",
]

_STEALTH_ENABLED: bool = True
_STEALTH_LAST_CHECK: float = 0
_STEALTH_CACHE: dict[str, dict] = {}
_STEALTH_CACHE_LOCK = threading.Lock()


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


def _domain_hash(domain: str, salt: str = "") -> int:
    """Deterministic hash for domain-based selection (same across Python runs)."""
    return int(hashlib.md5((domain + salt).encode()).hexdigest(), 16)


def _refresh_stealth_config() -> None:
    global _STEALTH_ENABLED, _STEALTH_LAST_CHECK
    now = time.time()
    if now - _STEALTH_LAST_CHECK < 120:
        return
    _STEALTH_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("playwright_stealth_enabled", "true")
            _STEALTH_ENABLED = val.lower() in ("true", "1", "yes")
    except Exception:
        _STEALTH_ENABLED = os.environ.get(
            "PLAYWRIGHT_STEALTH_ENABLED", "true"
        ).lower() in ("true", "1", "yes")


def stealth_for_domain(url: str) -> dict | None:
    """Return a deterministic stealth profile for the given URL's domain.

    Same domain always returns the same profile (UA, viewport, locale, etc.).
    Different domains return different profiles.
    Returns None when stealth is disabled.
    """
    _refresh_stealth_config()
    if not _STEALTH_ENABLED:
        return None

    domain = _extract_domain(url)
    with _STEALTH_CACHE_LOCK:
        cached = _STEALTH_CACHE.get(domain)
        if cached:
            return cached

    profile: dict = {
        "launch_args": _get_launch_args(),
        "user_agent": _STEALTH_USER_AGENTS[
            _domain_hash(domain, "ua") % len(_STEALTH_USER_AGENTS)
        ],
        "viewport": _STEALTH_VIEWPORTS[
            _domain_hash(domain, "vp") % len(_STEALTH_VIEWPORTS)
        ],
        "locale": _STEALTH_LOCALES[_domain_hash(domain, "loc") % len(_STEALTH_LOCALES)],
        "timezone_id": _STEALTH_TIMEZONES[
            _domain_hash(domain, "tz") % len(_STEALTH_TIMEZONES)
        ],
        "color_scheme": "light",
        "device_scale_factor": 1 if _domain_hash(domain, "dsf") % 3 != 0 else 2,
    }

    with _STEALTH_CACHE_LOCK:
        _STEALTH_CACHE[domain] = profile

    return profile


def _get_launch_args() -> list[str]:
    """Return Chromium CLI args for stealth."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-automation",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-features=IsolateOrigins,site-per-process",
    ]


def get_stealth_init_scripts() -> list[str]:
    """Return JS init scripts to inject before page load."""
    return [
        "Object.defineProperty(navigator, 'webdriver', { get: () => false });",
        "window.chrome = { runtime: {} };",
        "Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });",
        "Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });",
        "const _origQuery = window.navigator.permissions.query; window.navigator.permissions.query = (p) => p.name === 'notifications' ? Promise.resolve({ state: 'denied' }) : _origQuery(p);",
    ]


def apply_stealth_to_context(context) -> None:
    """Apply init scripts to a BrowserContext before navigation."""
    for script in get_stealth_init_scripts():
        context.add_init_script(script)


def reset_stealth_state() -> None:
    """Clear cached stealth profiles (for testing)."""
    global _STEALTH_LAST_CHECK
    with _STEALTH_CACHE_LOCK:
        _STEALTH_CACHE.clear()
    _STEALTH_LAST_CHECK = 0
