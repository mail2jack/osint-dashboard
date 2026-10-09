"""Email notifications for the customer portal intake workflow."""

import logging
from html import escape

from ..email_utils import is_smtp_configured, send_email
from ..models import ClientPortalAccess, Notification, User, db


def _create_in_app(user_id: str, tenant_id: str, title: str, message: str, link: str = "") -> None:
    db.session.add(
        Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            category="portal",
            title=title,
            message=message[:500],
            link=link,
        )
    )

logger = logging.getLogger(__name__)


def _send(recipient: str, subject: str, heading: str, message: str, link: str = "") -> bool:
    if not recipient or not is_smtp_configured():
        return False
    link_html = f'<p><a href="{escape(link, quote=True)}">Open in Iveras</a></p>' if link else ""
    body_html = (
        '<html><body style="font-family:sans-serif;padding:2rem;">'
        f"<h2>{escape(heading)}</h2><p>{escape(message)}</p>{link_html}"
        "</body></html>"
    )
    body_text = f"{heading}\n\n{message}"
    if link:
        body_text += f"\n\nOpen in Iveras: {link}"
    try:
        return send_email(recipient, subject, body_html, body_text)
    except Exception:
        logger.exception("Portal email notification failed for %s", recipient)
        return False


def notify_tenant_admins(intake, subject: str, heading: str, message: str, link: str = "") -> int:
    """Notify active tenant admins/owners about a new intake request."""
    admins = User.query.filter(
        User.tenant_id == intake.tenant_id,
        User.is_active.is_(True),
        User.role.in_(["admin", "owner"]),
        User.email.isnot(None),
    ).all()
    sent = 0
    for admin in admins:
        _create_in_app(admin.id, intake.tenant_id, heading, message, link)
        if _send(admin.email, subject, heading, message, link):
            sent += 1
    db.session.commit()
    return sent


def notify_requester(intake, subject: str, heading: str, message: str, link: str = "") -> bool:
    """Notify the portal requester about an intake status change."""
    email = getattr(intake.submitted_by, "email", None)
    requester_id = getattr(intake, "submitted_by_id", None)
    if requester_id:
        _create_in_app(requester_id, intake.tenant_id, heading, message, link)
    result = _send(email, subject, heading, message, link)
    db.session.commit()
    return result


def notify_case_portal_users(case, subject: str, heading: str, message: str, link: str = "") -> int:
    """Notify active portal users linked to a case's client."""
    access_rows = ClientPortalAccess.query.filter_by(
        tenant_id=case.tenant_id,
        client_id=case.client_id,
        is_active=True,
    ).all()
    user_ids = {row.user_id for row in access_rows if row.user_id}
    users = User.query.filter(User.id.in_(user_ids), User.is_active.is_(True)).all() if user_ids else []
    sent = 0
    for user in users:
        _create_in_app(user.id, case.tenant_id, heading, message, link)
        if _send(user.email, subject, heading, message, link):
            sent += 1
    db.session.commit()
    return sent


def notify_registration_received(registration) -> int:
    """Confirm a public registration and alert platform administrators."""
    subject = "Uw Iveras-aanvraag is ontvangen"
    heading = "Uw aanvraag is ontvangen"
    message = (
        "Iveras heeft uw aanvraag voor contact of klanttoegang ontvangen. "
        "We beoordelen welke vervolgstap het beste past en nemen daarna contact met u op."
    )
    sent = 1 if _send(registration.email, subject, heading, message) else 0
    administrators = User.query.filter(
        User.is_active.is_(True),
        User.is_super_admin.is_(True),
        User.email.isnot(None),
    ).all()
    for administrator in administrators:
        if _send(
            administrator.email,
            "Nieuwe publieke Iveras-aanvraag",
            "Nieuwe aanvraag ontvangen",
            f"Er is een nieuwe aanvraag ontvangen van {registration.contact_name} ({registration.email}).",
        ):
            sent += 1
    return sent
