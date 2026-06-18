"""
Simple webhook dispatch for external integrations.
Disabled by default — enable by setting webhook_urls in DB.
"""

import concurrent.futures
import json
import logging
import hmac
import hashlib
from datetime import datetime, timezone

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


def _send_one(url: str, body: str, headers: dict) -> dict:
    try:
        r = httpx.post(url, content=body, headers=headers, timeout=10)
        return {"url": url, "status": r.status_code, "ok": r.is_success}
    except Exception as e:
        logger.exception("Webhook delivery failed to %s", url)
        return {"url": url, "error": str(e), "ok": False}


def dispatch(event: str, payload: dict) -> list[dict]:
    """Dispatch an event to all configured webhook URLs (parallel via thread pool)."""
    try:
        from .models import Setting

        urls = Setting.get("webhook_urls", [])
        secret = Setting.get("webhook_secret", "")
    except Exception:
        return []
    if not urls:
        return []

    body = _build_body(event, payload)
    headers = _build_headers(secret, body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 10)) as pool:
        futures = [pool.submit(_send_one, url, body, headers) for url in urls]
        return [f.result() for f in futures]
