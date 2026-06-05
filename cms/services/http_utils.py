import logging
import os
import random
import threading
import time
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests
from curl_cffi import CurlError

logger = logging.getLogger(__name__)

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


def get_next_proxy() -> dict[str, str] | None:
    global _PROXY_INDEX
    _refresh_proxy_config()
    if not _PROXY_ENABLED or not _PROXY_LIST:
        return None
    with _proxy_lock:
        proxy = _PROXY_LIST[_PROXY_INDEX % len(_PROXY_LIST)]
        _PROXY_INDEX = (_PROXY_INDEX + 1) % len(_PROXY_LIST)
    return {"http": proxy, "https": proxy}


def reset_proxy_state() -> None:
    global _PROXY_INDEX
    with _proxy_lock:
        _PROXY_INDEX = 0
        _PROXY_LAST_CHECK = 0


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


def jittered_get(url: str, **kwargs: Any) -> curl_requests.Response:
    jitter_sleep(domain_hint=url)
    proxies = get_next_proxy()
    if proxies:
        kwargs.setdefault("proxies", proxies)
    kwargs.setdefault("impersonate", next_impersonate())
    try:
        return curl_requests.get(url, **kwargs)
    except (CurlError, Exception) as e:
        logger.debug(f"curl_cffi GET failed for {url}: {e}")
        fallback = _try_playwright_fallback(url, method="GET", **kwargs)
        if fallback is not None:
            return fallback
        raise


def jittered_post(url: str, **kwargs: Any) -> curl_requests.Response:
    jitter_sleep(domain_hint=url)
    proxies = get_next_proxy()
    if proxies:
        kwargs.setdefault("proxies", proxies)
    kwargs.setdefault("impersonate", next_impersonate())
    try:
        return curl_requests.post(url, **kwargs)
    except (CurlError, Exception) as e:
        logger.debug(f"curl_cffi POST failed for {url}: {e}")
        fallback = _try_playwright_fallback(url, method="POST", **kwargs)
        if fallback is not None:
            return fallback
        raise


def jittered_head(url: str, **kwargs: Any) -> curl_requests.Response:
    jitter_sleep(domain_hint=url)
    proxies = get_next_proxy()
    if proxies:
        kwargs.setdefault("proxies", proxies)
    kwargs.setdefault("impersonate", next_impersonate())
    try:
        return curl_requests.head(url, **kwargs)
    except (CurlError, Exception) as e:
        logger.debug(f"curl_cffi HEAD failed for {url}: {e}")
        fallback = _try_playwright_fallback(url, method="HEAD", **kwargs)
        if fallback is not None:
            return fallback
        raise


def jittered_session(
    timeout: float = 10.0, headers: dict | None = None
) -> curl_requests.Session:
    """Return a curl_cffi.Session with jitter + proxy + profile rotation."""
    kwargs: dict = {"impersonate": next_impersonate(), "timeout": timeout}
    proxies = get_next_proxy()
    if proxies:
        kwargs["proxies"] = proxies
    sess = curl_requests.Session(**kwargs)
    if headers:
        sess.headers.update(headers)
    return sess
