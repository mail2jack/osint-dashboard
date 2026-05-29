"""
Simple webhook dispatch for external integrations.
Disabled by default — enable by setting webhook_urls in DB.
"""

import json
import logging
import hmac
import hashlib
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def dispatch(event: str, payload: dict) -> list[dict]:
    """Dispatch an event to all configured webhook URLs. Returns per-URL results."""
    try:
        from .models import Setting

        urls = Setting.get("webhook_urls", [])
        secret = Setting.get("webhook_secret", "")
    except Exception:
        return []
    if not urls:
        return []

    import httpx

    results = []
    body = json.dumps(
        {
            "event": event,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        default=str,
    )

    for url in urls:
        try:
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Iveras-OSINT-Webhook/1.0",
            }
            if secret:
                sig = hmac.new(
                    secret.encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = sig
            r = httpx.post(url, content=body, headers=headers, timeout=10)
            results.append({"url": url, "status": r.status_code, "ok": r.is_success})
        except Exception as e:
            results.append({"url": url, "error": str(e), "ok": False})
    return results
