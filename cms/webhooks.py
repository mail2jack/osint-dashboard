"""
Simple webhook dispatch for external integrations.
Disabled by default — enable by setting webhook_urls in DB.
"""

import concurrent.futures
import json
import logging
import hmac
import hashlib
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def _build_body(event: str, payload: dict) -> str:
    return json.dumps(
        {
            "event": event,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        default=str,
    )


def _build_headers(secret: str, body: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Iveras-OSINT-Webhook/1.0",
    }
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig
    return headers


def _valid_urls(urls: object) -> list[str]:
    """Keep webhook delivery limited to explicit HTTP(S) destinations."""
    if not isinstance(urls, list):
        return []
    valid = []
    for candidate in urls[:50]:
        if not isinstance(candidate, str):
            continue
        url = candidate.strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        if parsed.username or parsed.password:
            continue
        hostname = parsed.hostname
        if not hostname or hostname.lower() in {"localhost", "localhost.localdomain"}:
            continue
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            continue
        if url not in valid:
            valid.append(url)
    return valid


def _send_one(url: str, body: str, headers: dict) -> dict:
    try:
        r = httpx.post(url, content=body, headers=headers, timeout=10)
        return {"url": url, "status": r.status_code, "ok": r.is_success}
    except Exception as e:
        logger.exception("Webhook delivery failed to %s", url)
        return {"url": url, "error": str(e), "ok": False}


def dispatch(event: str, payload: dict, *, tenant_id: str | None = None) -> list[dict]:
    """Dispatch an event to all configured webhook URLs (parallel via thread pool)."""
    try:
        from flask import g
        from flask_login import current_user
        from .models import TenantSetting

        scoped_tenant_id = tenant_id or getattr(g, "tenant_id", None)
        if not scoped_tenant_id and current_user.is_authenticated:
            scoped_tenant_id = current_user.tenant_id
        if not scoped_tenant_id:
            return []
        urls = TenantSetting.get("webhook_urls", [], tenant_id=scoped_tenant_id)
        secret = TenantSetting.get("webhook_secret", "", tenant_id=scoped_tenant_id)
        if isinstance(urls, str):
            try:
                urls = json.loads(urls)
            except (TypeError, ValueError):
                urls = []
    except Exception:
        return []
    urls = _valid_urls(urls)
    if not urls:
        return []

    body = _build_body(event, payload)
    headers = _build_headers(secret, body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 10)) as pool:
        futures = [pool.submit(_send_one, url, body, headers) for url in urls]
        return [f.result() for f in futures]
