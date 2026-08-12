import logging
import os
import time
from typing import Any

from cms.routes.utils import is_safe_url
from cms.services.http_utils import get_next_proxy

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE: bool | None = None
_PLAYWRIGHT_ENABLED: bool | None = None
_PLAYWRIGHT_LAST_CHECK: float = 0


class PlaywrightResponse:
    """Mimics curl_cffi.Response for Playwright fallback callers."""

    def __init__(
        self, status_code: int, text: str, url: str, headers: dict | None = None
    ):
        self.status_code = status_code
        self._text = text
        self.url = url
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300
        self.content = text.encode("utf-8")

    def json(self) -> Any:
        import json

        return json.loads(self._text)

    def raise_for_status(self) -> None:
        if not self.ok:
            from curl_cffi import CurlError

            raise CurlError(f"HTTP {self.status_code}")


def _refresh_playwright_config() -> None:
    global _PLAYWRIGHT_ENABLED, _PLAYWRIGHT_AVAILABLE, _PLAYWRIGHT_LAST_CHECK
    now = time.time()
    if now - _PLAYWRIGHT_LAST_CHECK < 60:
        return
    _PLAYWRIGHT_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("playwright_fallback_enabled", "false")
            _PLAYWRIGHT_ENABLED = val.lower() in ("true", "1", "yes")
    except Exception:
        _PLAYWRIGHT_ENABLED = os.environ.get(
            "PLAYWRIGHT_FALLBACK_ENABLED", "false"
        ).lower() in ("true", "1", "yes")

    if _PLAYWRIGHT_ENABLED is None:
        _PLAYWRIGHT_ENABLED = False

    if _PLAYWRIGHT_AVAILABLE is None:
        try:
            import playwright  # noqa: F401

            _PLAYWRIGHT_AVAILABLE = True
        except ImportError:
            _PLAYWRIGHT_AVAILABLE = False


def playwright_fetch(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: str | None = None,
    timeout_ms: int = 30000,
) -> PlaywrightResponse | None:
    _refresh_playwright_config()
    if not _PLAYWRIGHT_ENABLED or not _PLAYWRIGHT_AVAILABLE:
        return None

    try:
        from playwright.sync_api import sync_playwright
        from cms.services.playwright_stealth import (
            stealth_for_domain,
            apply_stealth_to_context,
        )

        with sync_playwright() as p:
            stealth = stealth_for_domain(url)
            launch_kwargs: dict = {"headless": True, "timeout": timeout_ms}
            if stealth:
                launch_kwargs["args"] = list(stealth["launch_args"])
                launch_kwargs["args"].append(
                    f"--window-size={stealth['viewport']['width']},{stealth['viewport']['height']}"
                )

            browser = p.chromium.launch(**launch_kwargs)

            context_kwargs: dict = {}

            proxies = get_next_proxy()
            if proxies:
                proxy_url = proxies.get("https") or proxies.get("http") or ""
                if proxy_url:
                    context_kwargs["proxy"] = {"server": proxy_url}

            if stealth:
                context_kwargs["user_agent"] = stealth["user_agent"]
                context_kwargs["viewport"] = dict(stealth["viewport"])
                context_kwargs["locale"] = stealth["locale"]
                context_kwargs["timezone_id"] = stealth["timezone_id"]
                context_kwargs["color_scheme"] = stealth["color_scheme"]
                context_kwargs["device_scale_factor"] = stealth["device_scale_factor"]
            else:
                context_kwargs["user_agent"] = (
                    headers.get("User-Agent")
                    if headers
                    else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )

            context = browser.new_context(**context_kwargs)

            if headers:
                extra = dict(headers)
                extra.pop("User-Agent", None)
                extra.pop("user-agent", None)
                if extra:
                    context.set_extra_http_headers(extra)

            page = context.new_page()

            if stealth:
                apply_stealth_to_context(context)

            from cms.services.ssrf_guard import install_request_guard

            install_request_guard(page)

            resp = None
            try:
                if not is_safe_url(url):
                    raise ValueError("Blocked URL")
                if method == "GET":
                    resp = page.goto(
                        url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                elif method == "POST":
                    resp = page.goto(
                        url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                    if data:
                        page.evaluate(f"navigator.sendBeacon('{url}', '{data}')")
                else:
                    resp = page.goto(
                        url, wait_until="domcontentloaded", timeout=timeout_ms
                    )

                page.wait_for_timeout(1000)
                content = page.content()
                status = resp.status if resp else 200
                resp_headers = dict(resp.headers) if resp and resp.headers else {}
            except Exception as e:
                logger.debug(f"Playwright fetch failed for {url}: {e}")
                return None
            finally:
                browser.close()

        return PlaywrightResponse(
            status_code=status,
            text=content,
            url=resp.url if resp else url,
            headers=resp_headers,
        )
    except ImportError:
        logger.debug("Playwright not installed, skipping fallback")
        return None
    except Exception as e:
        logger.warning(f"Playwright fetch error for {url}: {e}")
        return None


def is_playwright_available() -> bool:
    _refresh_playwright_config()
    return bool(_PLAYWRIGHT_ENABLED and _PLAYWRIGHT_AVAILABLE)
