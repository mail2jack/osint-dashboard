"""
Notification system — sends webhooks for important events.
Webhook URL is configured via Setting 'webhook_url'.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_webhook_url() -> str | None:
    from .models import Setting
    return Setting.get('webhook_url') or None


def send_webhook(event: str, payload: dict) -> bool:
    """Send a webhook POST for a system event. Returns True on success."""
    url = _get_webhook_url()
    if not url:
        return False
    try:
        import httpx
        body = {
            'event': event,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'payload': payload,
        }
        resp = httpx.post(url, json=body, timeout=10)
        resp.raise_for_status()
        logger.info(f"Webhook sent: {event}")
        return True
    except Exception as e:
        logger.warning(f"Webhook failed for {event}: {e}")
        return False


def notify_login_failed(username: str, ip_address: str):
    send_webhook('login.failed', {'username': username, 'ip': ip_address, 'timestamp': datetime.now(timezone.utc).isoformat()})


def notify_login_success(username: str, ip_address: str):
    send_webhook('login.success', {'username': username, 'ip': ip_address})


def notify_user_created(username: str, email: str, created_by: str):
    send_webhook('user.created', {'username': username, 'email': email, 'created_by': created_by})


def notify_case_created(case_id: str, title: str, created_by: str):
    send_webhook('case.created', {'case_id': case_id, 'title': title, 'created_by': created_by})


def notify_account_locked(username: str, ip_address: str, duration_minutes: int):
    send_webhook('account.locked', {'username': username, 'ip': ip_address, 'duration_minutes': duration_minutes})
