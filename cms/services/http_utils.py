import logging
import os
import random
import threading
import time
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests
from curl_cffi import CurlError

_FALLBACK_IMPROFILE = "chrome124"

logger = logging.getLogger(__name__)


class TorNotAvailableError(Exception):
    """Raised when Tor is required but not available in strict mode."""


class SSRFBlockedError(Exception):
    """Raised when a URL or redirect target resolves to a blocked address."""


_JITTER_ENABLED: bool | None = None
_JITTER_MIN: float = 0.5
_JITTER_MAX: float = 3.0
_JITTER_LAST_CHECK: float = 0

_DOMAIN_LAST_CALL: dict[str, float] = {}
_domain_lock = threading.Lock()

_PROXY_LIST: list[str] = []
_PROXY_INDEX: int = 0
_PROXY_ENABLED: bool = False
_PROXY_LAST_CHECK: float = 0
_proxy_lock = threading.Lock()

_TOR_ENABLED: bool = False
_TOR_PROXY: str = "socks5h://127.0.0.1:9050"
_TOR_STRICT: bool = False
_TOR_LAST_CHECK: float = 0

_IMPROFILE_LIST = [
    "chrome124",
    "chrome123",
    "chrome120",
    "chrome116",
    "chrome110",
    "safari17_2_1",
    "safari17_0",
    "firefox123",
    "firefox120",
]
_IMPROFILE_INDEX: int = 0
_IMPROFILE_ROTATION: bool = True
_IMPROFILE_LAST_CHECK: float = 0

_DOMAIN_IMPERSONATION_ENABLED: bool = True
_DOMAIN_TO_PROFILE: dict[str, str] = {}
_DOMAIN_IMPERSONATION_LAST_CHECK: float = 0


def _refresh_jitter_config() -> None:
    global _JITTER_ENABLED, _JITTER_MIN, _JITTER_MAX, _JITTER_LAST_CHECK
    now = time.time()
    if now - _JITTER_LAST_CHECK < 60:
        return
    _JITTER_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("jitter_enabled", "true")
            _JITTER_ENABLED = val.lower() in ("true", "1", "yes")
            _JITTER_MIN = float(Setting.get("jitter_min", "0.3"))
            _JITTER_MAX = float(Setting.get("jitter_max", "2.0"))
    except Exception:
        _JITTER_ENABLED = os.environ.get("JITTER_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        _JITTER_MIN = float(os.environ.get("JITTER_MIN", "0.3"))
        _JITTER_MAX = float(os.environ.get("JITTER_MAX", "2.0"))

    _JITTER_MIN = max(0.0, _JITTER_MIN)
    _JITTER_MAX = max(_JITTER_MIN + 0.1, _JITTER_MAX)


def jitter_sleep(domain_hint: str | None = None) -> None:
    _refresh_jitter_config()
    if not _JITTER_ENABLED:
        return

    domain = _extract_domain(domain_hint) if domain_hint else "__global__"
    now = time.time()

    with _domain_lock:
        last = _DOMAIN_LAST_CALL.get(domain, 0.0)
        elapsed = now - last
        delay_needed = _JITTER_MIN - elapsed

    if delay_needed > 0:
        jitter = random.uniform(_JITTER_MIN, _JITTER_MAX)
        time.sleep(jitter)
        now = time.time()
    else:
        jitter = random.uniform(0, _JITTER_MAX - _JITTER_MIN)
        if jitter > 0.1:
            time.sleep(jitter)
            now = time.time()

    with _domain_lock:
        _DOMAIN_LAST_CALL[domain] = now


def _extract_domain(url: str | None) -> str:
    if not url:
        return "__global__"
    try:
        parsed = urlparse(url)
        return parsed.hostname or "__global__"
    except Exception:
        return "__global__"


def reset_jitter_state() -> None:
    with _domain_lock:
        _DOMAIN_LAST_CALL.clear()


def _refresh_tor_config() -> None:
    global _TOR_ENABLED, _TOR_PROXY, _TOR_STRICT, _TOR_LAST_CHECK
    now = time.time()
    if now - _TOR_LAST_CHECK < 60:
        return
    _TOR_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("tor_enabled", "false")
            _TOR_ENABLED = val.lower() in ("true", "1", "yes")
            _TOR_PROXY = Setting.get("tor_proxy", "socks5h://127.0.0.1:9050").strip()
            val = Setting.get("tor_strict_mode", "false")
            _TOR_STRICT = val.lower() in ("true", "1", "yes")
    except Exception:
        _TOR_ENABLED = os.environ.get("TOR_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        _TOR_PROXY = os.environ.get("TOR_PROXY", "socks5h://127.0.0.1:9050").strip()
        _TOR_STRICT = os.environ.get("TOR_STRICT_MODE", "false").lower() in (
            "true",
            "1",
            "yes",
        )


def _refresh_proxy_config() -> None:
    global _PROXY_LIST, _PROXY_ENABLED, _PROXY_INDEX, _PROXY_LAST_CHECK
    now = time.time()
    if now - _PROXY_LAST_CHECK < 60:
        return
    _PROXY_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("proxy_rotation_enabled", "false")
            _PROXY_ENABLED = val.lower() in ("true", "1", "yes")
            raw = Setting.get("proxy_list", "")
            _PROXY_LIST = [
                p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()
            ]
    except Exception:
        _PROXY_ENABLED = os.environ.get("PROXY_ROTATION_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        raw = os.environ.get("PROXY_LIST", "")
        _PROXY_LIST = [
            p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()
        ]
    if _PROXY_LIST:
        _PROXY_INDEX = min(_PROXY_INDEX, len(_PROXY_LIST) - 1)
    else:
        _PROXY_ENABLED = False


def get_next_proxy(identity: str | None = None) -> dict[str, str] | None:
    global _PROXY_INDEX
    _refresh_tor_config()
    _refresh_proxy_config()

    # Tor heeft voorrang op proxy rotatie
    if _TOR_ENABLED and _TOR_PROXY:
        proxy = _TOR_PROXY
        if identity:
            from cms.services.identity_isolation import identity_for_proxy

            proxy = identity_for_proxy(proxy, identity)
        return {"http": proxy, "https": proxy}

    # Proxy rotatie fallback
    if _PROXY_ENABLED and _PROXY_LIST:
        with _proxy_lock:
            proxy = _PROXY_LIST[_PROXY_INDEX % len(_PROXY_LIST)]
            _PROXY_INDEX = (_PROXY_INDEX + 1) % len(_PROXY_LIST)
        return {"http": proxy, "https": proxy}

    # Strict mode: weiger als Tor niet beschikbaar is
    if _TOR_STRICT:
        raise TorNotAvailableError(
            "Tor strict mode enabled but Tor is not available. "
            "Enable Tor or disable tor_strict_mode."
        )

    return None


def reset_proxy_state() -> None:
    global _PROXY_INDEX
    with _proxy_lock:
        _PROXY_INDEX = 0
        _PROXY_LAST_CHECK = 0


def is_tor_enabled() -> bool:
    _refresh_tor_config()
    return _TOR_ENABLED


def get_tor_proxy() -> str | None:
    _refresh_tor_config()
    return _TOR_PROXY if _TOR_ENABLED else None


def reset_tor_state() -> None:
    global _TOR_LAST_CHECK, _TOR_ENABLED, _TOR_STRICT
    _TOR_LAST_CHECK = 0
    _TOR_ENABLED = False
    _TOR_STRICT = False


def _refresh_impersonate_config() -> None:
    global _IMPROFILE_LIST, _IMPROFILE_ROTATION, _IMPROFILE_INDEX, _IMPROFILE_LAST_CHECK
    now = time.time()
    if now - _IMPROFILE_LAST_CHECK < 120:
        return
    _IMPROFILE_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("impersonate_rotation_enabled", "true")
            _IMPROFILE_ROTATION = val.lower() in ("true", "1", "yes")
            raw = Setting.get("impersonate_profiles", "")
            if raw:
                _IMPROFILE_LIST = [
                    p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()
                ]
    except Exception:
        _IMPROFILE_ROTATION = os.environ.get(
            "IMPROFILE_ROTATION_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
    if not _IMPROFILE_LIST:
        _IMPROFILE_LIST = [
            "chrome124",
            "chrome123",
            "chrome120",
            "chrome116",
            "chrome110",
            "safari17_2_1",
            "safari17_0",
            "firefox123",
            "firefox120",
        ]


def next_impersonate() -> str:
    global _IMPROFILE_INDEX
    _refresh_impersonate_config()
    if not _IMPROFILE_ROTATION or len(_IMPROFILE_LIST) <= 1:
        return "chrome124"
    with _proxy_lock:
        profile = _IMPROFILE_LIST[_IMPROFILE_INDEX % len(_IMPROFILE_LIST)]
        _IMPROFILE_INDEX = (_IMPROFILE_INDEX + 1) % len(_IMPROFILE_LIST)
    return profile


def _extract_domain(url: str) -> str:
    """Extract the hostname from a URL for domain-based profile mapping."""
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


def _refresh_domain_impersonation_config() -> None:
    global _DOMAIN_IMPERSONATION_ENABLED, _DOMAIN_IMPERSONATION_LAST_CHECK
    now = time.time()
    if now - _DOMAIN_IMPERSONATION_LAST_CHECK < 120:
        return
    _DOMAIN_IMPERSONATION_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("domain_impersonation_enabled", "true")
            _DOMAIN_IMPERSONATION_ENABLED = val.lower() in ("true", "1", "yes")
    except Exception:
        _DOMAIN_IMPERSONATION_ENABLED = os.environ.get(
            "DOMAIN_IMPERSONATION_ENABLED", "true"
        ).lower() in ("true", "1", "yes")


def impersonate_for_domain(url: str) -> str:
    """Return a consistent impersonation profile for a given URL's domain.

    Same domain always gets the same profile, different domains get
    different profiles. This prevents fingerprint correlation across domains
    while appearing as a consistent browser to each domain.
    """
    _refresh_impersonate_config()
    _refresh_domain_impersonation_config()

    if not _DOMAIN_IMPERSONATION_ENABLED or len(_IMPROFILE_LIST) <= 1:
        return next_impersonate()

    domain = _extract_domain(url)
    cached = _DOMAIN_TO_PROFILE.get(domain)
    if cached:
        return cached

    idx = hash(domain) % len(_IMPROFILE_LIST)
    profile = _IMPROFILE_LIST[idx]
    _DOMAIN_TO_PROFILE[domain] = profile
    return profile


def reset_impersonation_state() -> None:
    """Clear cached domain→profile mapping (for testing)."""
    global _IMPROFILE_INDEX, _DOMAIN_TO_PROFILE, _DOMAIN_IMPERSONATION_LAST_CHECK
    _IMPROFILE_INDEX = 0
    _DOMAIN_TO_PROFILE.clear()
    _DOMAIN_IMPERSONATION_LAST_CHECK = 0


def _try_playwright_fallback(url: str, method: str = "GET", **kwargs: Any) -> Any:
    try:
        from cms.services.playwright_service import playwright_fetch

        pw_result = playwright_fetch(
            url,
            method=method,
            headers=kwargs.get("headers"),
            data=kwargs.get("data"),
            timeout_ms=int(kwargs.get("timeout", 10) * 1000),
        )
        if pw_result is not None:
            return pw_result
    except Exception:
        logger.debug(f"Playwright fallback unavailable for {url}")
    return None


def _raise_if_tor_strict_fail(proxies: dict | None) -> None:
    """If Tor strict mode is enabled and we were using Tor, raise TorNotAvailableError."""
    if _TOR_STRICT and proxies and "socks5" in proxies.get("http", ""):
        raise TorNotAvailableError(
            "Tor strict mode enabled but Tor request failed. "
            "Check Tor proxy or disable tor_strict_mode."
        )


def _record_audit(
    url: str,
    method: str,
    status: int | str,
    kwargs: dict,
    error: str | None = None,
) -> None:
    try:
        from cms.services.audit_chain import record_osint_call

        record_osint_call(
            url=url,
            method=method,
            status_code=status,
            domain=_extract_domain(url),
            profile=kwargs.get("impersonate"),
            error=error,
        )
    except Exception:
        logger.debug("Audit chain recording failed", exc_info=True)


def _get_identity() -> str | None:
    try:
        from cms.services.identity_isolation import get_current_identity

        return get_current_identity()
    except Exception:
        return None


def _impersonated_request(
    method: str,
    url: str,
    kwargs: dict,
    proxies: dict | None,
) -> curl_requests.Response:
    """Make a curl_cffi request; retry with fallback profile if unsupported."""
    try:
        resp = getattr(curl_requests, method)(url, **kwargs)
        return resp
    except CurlError as e:
        if "not supported" in str(e):
            kwargs["impersonate"] = _FALLBACK_IMPROFILE
            return getattr(curl_requests, method)(url, **kwargs)
        raise


def _safe_fetch(
    method: str,
    url: str,
    kwargs: dict,
    proxies: dict | None,
) -> curl_requests.Response:
    """Fetch following redirects but revalidate each hop against the SSRF guard."""
    from urllib.parse import urljoin

    from cms.services.ssrf_guard import validate_url

    max_hops = int(kwargs.pop("max_redirects", 10))
    allow_redirects = bool(kwargs.pop("allow_redirects", True))
    current = url
    for _ in range(max_hops + 1):
        ok, reason = validate_url(current)
        if not ok:
            raise SSRFBlockedError(f"{current}: {reason}")
        kwargs["allow_redirects"] = False
        resp = _impersonated_request(method, current, kwargs, proxies)
        is_redirect = getattr(resp, "is_redirect", None)
        if is_redirect is None:
            is_redirect = 300 <= resp.status_code < 400
        location = getattr(resp, "headers", None)
        location = location.get("Location") if location else None
        if allow_redirects and is_redirect and isinstance(location, str) and location:
            current = urljoin(current, location)
            continue
        return resp
    raise SSRFBlockedError(f"too many redirects from {url}")


def jittered_get(url: str, **kwargs: Any) -> curl_requests.Response:
    jitter_sleep(domain_hint=url)
    identity = _get_identity()
    proxies = get_next_proxy(identity=identity)
    if proxies:
        kwargs.setdefault("proxies", proxies)
    kwargs.setdefault("impersonate", impersonate_for_domain(url))
    try:
        resp = _safe_fetch("get", url, kwargs, proxies)
        _record_audit(url, "GET", resp.status_code, kwargs)
        return resp
    except TorNotAvailableError:
        _record_audit(url, "GET", "TOR_BLOCKED", kwargs)
        raise
    except (CurlError, Exception) as e:
        logger.debug(f"curl_cffi GET failed for {url}: {e}")
        _raise_if_tor_strict_fail(proxies)
        fallback = _try_playwright_fallback(url, method="GET", **kwargs)
        if fallback is not None:
            _record_audit(url, "GET", fallback.status_code, kwargs)
            return fallback
        _record_audit(url, "GET", "ERROR", kwargs, error=str(e))
        raise


def jittered_post(url: str, **kwargs: Any) -> curl_requests.Response:
    jitter_sleep(domain_hint=url)
    identity = _get_identity()
    proxies = get_next_proxy(identity=identity)
    if proxies:
        kwargs.setdefault("proxies", proxies)
    kwargs.setdefault("impersonate", impersonate_for_domain(url))
    try:
        resp = _safe_fetch("post", url, kwargs, proxies)
        _record_audit(url, "POST", resp.status_code, kwargs)
        return resp
    except TorNotAvailableError:
        _record_audit(url, "POST", "TOR_BLOCKED", kwargs)
        raise
    except (CurlError, Exception) as e:
        logger.debug(f"curl_cffi POST failed for {url}: {e}")
        _raise_if_tor_strict_fail(proxies)
        fallback = _try_playwright_fallback(url, method="POST", **kwargs)
        if fallback is not None:
            _record_audit(url, "POST", fallback.status_code, kwargs)
            return fallback
        _record_audit(url, "POST", "ERROR", kwargs, error=str(e))
        raise


def jittered_head(url: str, **kwargs: Any) -> curl_requests.Response:
    jitter_sleep(domain_hint=url)
    identity = _get_identity()
    proxies = get_next_proxy(identity=identity)
    if proxies:
        kwargs.setdefault("proxies", proxies)
    kwargs.setdefault("impersonate", impersonate_for_domain(url))
    try:
        resp = _safe_fetch("head", url, kwargs, proxies)
        _record_audit(url, "HEAD", resp.status_code, kwargs)
        return resp
    except TorNotAvailableError:
        _record_audit(url, "HEAD", "TOR_BLOCKED", kwargs)
        raise
    except (CurlError, Exception) as e:
        logger.debug(f"curl_cffi HEAD failed for {url}: {e}")
        _raise_if_tor_strict_fail(proxies)
        fallback = _try_playwright_fallback(url, method="HEAD", **kwargs)
        if fallback is not None:
            _record_audit(url, "HEAD", fallback.status_code, kwargs)
            return fallback
        _record_audit(url, "HEAD", "ERROR", kwargs, error=str(e))
        raise


def jittered_session(
    timeout: float = 10.0, headers: dict | None = None
) -> curl_requests.Session:
    """Return a curl_cffi.Session with jitter + proxy + profile rotation."""
    impersonate = next_impersonate()
    kwargs: dict = {"impersonate": impersonate, "timeout": timeout}
    proxies = get_next_proxy()
    if proxies:
        kwargs["proxies"] = proxies
    try:
        sess = curl_requests.Session(**kwargs)
    except CurlError as e:
        if "not supported" in str(e):
            kwargs["impersonate"] = _FALLBACK_IMPROFILE
            sess = curl_requests.Session(**kwargs)
        else:
            raise
    if headers:
        sess.headers.update(headers)
    return sess
