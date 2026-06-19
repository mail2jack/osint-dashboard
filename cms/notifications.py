"""
Notification system — sends webhooks for important events.
Webhook URL is configured via Setting 'webhook_url'.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_webhook_url() -> str | None:
    from .models import Setting

    return Setting.get("webhook_url") or None


def send_webhook(event: str, payload: dict) -> bool:
    """Send a webhook POST for a system event. Returns True on success."""
    url = _get_webhook_url()
    if not url:
        return False
    try:
        import httpx

        body = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        resp = httpx.post(url, json=body, timeout=10)
        resp.raise_for_status()
        logger.info(f"Webhook sent: {event}")
        return True
    except Exception as e:
        logger.warning(f"Webhook failed for {event}: {e}")
        return False


def notify_login_failed(username: str, ip_address: str):
    send_webhook(
        "login.failed",
        {
            "username": username,
            "ip": ip_address,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def notify_login_success(username: str, ip_address: str):
    send_webhook("login.success", {"username": username, "ip": ip_address})


def notify_signup(username: str, email: str, org_name: str):
    send_webhook(
        "user.signup",
        {"username": username, "email": email, "organization": org_name},
    )


def notify_user_created(username: str, email: str, created_by: str):
    send_webhook(
        "user.created", {"username": username, "email": email, "created_by": created_by}
    )


def notify_case_created(case_id: str, title: str, created_by: str):
    send_webhook(
        "case.created", {"case_id": case_id, "title": title, "created_by": created_by}
    )


def notify_account_locked(username: str, ip_address: str, duration_minutes: int):
    send_webhook(
        "account.locked",
        {"username": username, "ip": ip_address, "duration_minutes": duration_minutes},
    )


def notify_search_restricted(
    user_id: str,
    query: str,
    restricted_case_numbers: list[str],
    restricted_count: int,
    searching_username: str = "Unknown",
) -> list[str]:
    """Notify case owners when a user's search hit cases they can't access.

    Also notifies the searching user. Returns the list of case owner names.
    """
    try:
        from flask_login import current_user
        from .models import Notification, Case, User, db, case_assignments
    except Exception:
        return []

    owner_names: set[str] = set()

    for case_number in restricted_case_numbers:
        case = Case.query.filter(
            Case.case_number == case_number,
            Case.tenant_id == current_user.tenant_id,
        ).first()
        if not case:
            continue

        # Collect owner names for the notification to the searching user
        if case.lead_investigator_id:
            owner = db.session.get(User, case.lead_investigator_id)
            if owner:
                owner_names.add(owner.full_name or owner.username)
        if case.created_by and case.created_by != case.lead_investigator_id:
            owner = db.session.get(User, case.created_by)
            if owner:
                owner_names.add(owner.full_name or owner.username)

        # Find assigned users who should be notified
        target_user_ids = set()
        # Lead investigator
        if case.lead_investigator_id:
            target_user_ids.add(case.lead_investigator_id)
        # Direct assignee
        if case.assigned_to:
            target_user_ids.add(case.assigned_to)
        # Creator
        if case.created_by:
            target_user_ids.add(case.created_by)
        # Case_assignments table
        target_user_ids.update(
            r[0]
            for r in db.session.query(case_assignments.c.user_id)
            .filter(case_assignments.c.case_id == case.id)
            .all()
        )
        # Notify admins too
        for admin in User.query.filter_by(role="admin").all():
            target_user_ids.add(admin.id)
        for uid in target_user_ids:
            n = Notification(
                user_id=uid,
                message=f'🔍 User "{searching_username}" searched for "{query}" — found {restricted_count} result(s) in case {case_number} that were filtered',
                link=f"/cases/{case.id}",
            )
            db.session.add(n)

    # Notify the searching user about the restriction
    owners_str = ", ".join(sorted(owner_names)) if owner_names else "the case owner"
    searching_notification = Notification(
        user_id=user_id,
        message=f'🔍 Your search for "{query}" matched restricted content. '
        f"Case owner(s) ({owners_str}) have been notified and will contact you if needed.",
        link=None,
    )
    db.session.add(searching_notification)
    db.session.commit()

    return list(owner_names)
