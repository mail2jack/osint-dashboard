"""RQ task definitions for background workers.

These functions run in the RQ worker process (separate from the web process).
Each task rebuilds its own Flask app context.  No Flask globals
(current_app, request, g) are available at module level — they are
acquired inside the function body.
"""

from typing import Any
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_app():
    """Build a Flask app instance inside the worker process."""
    from app import app as _app

    return _app


def send_email_task(email: str, subject: str, body: str) -> bool:
    """Send an email via the worker process."""
    from cms.email_utils import send_email as _send

    app = _get_app()
    with app.app_context():
        try:
            _send(email, subject, body)
            logger.info("Email sent to %s via RQ worker", email)
            return True
        except Exception:
            logger.exception("Failed to send email to %s via RQ worker", email)
            return False


def send_password_reset_task(
    email: str, username: str, full_name: str, reset_url: str
) -> bool:
    """Send a password-reset email via the worker process."""
    from cms.email_utils import send_password_reset_email as _send

    app = _get_app()
    with app.app_context():
        try:
            _send(email, username, full_name, reset_url)
            logger.info("Password-reset email sent to %s via RQ worker", email)
            return True
        except Exception:
            logger.exception("Failed to send password-reset email to %s", email)
            return False


def send_notification_task(
    user_id: str,
    notification_type: str,
    title: str,
    message: str,
    action_url: str | None = None,
) -> bool:
    """Create and optionally email a notification via the worker process."""
    app = _get_app()
    with app.app_context():
        from cms.models import db, User, Notification

        try:
            user = db.session.get(User, user_id)
            if not user:
                logger.warning("Notification task: user %s not found", user_id)
                return False
            notification = Notification(
                user_id=user_id,
                type=notification_type,
                title=title,
                message=message,
                action_url=action_url or "",
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(notification)
            db.session.commit()
            logger.info(
                "Notification %s created for user %s via RQ worker",
                notification_type,
                user_id,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to create notification for user %s",
                user_id,
            )
            return False


def run_background_task(
    task_id: str,
    func_module: str,
    func_name: str,
    args: tuple = (),
    kwargs: dict | None = None,
) -> Any:
    """Generic RQ task that rebuilds Flask app context and delegates.

    Called by the RQ worker process.  The function was originally enqueued
    by ``background.run_in_background`` when Redis was available.
    """
    import importlib
    from cms.background import _run_task

    kwargs = kwargs or {}
    module = importlib.import_module(func_module)
    func = getattr(module, func_name)

    app = _get_app()
    with app.app_context():
        _run_task(task_id, func, *args, **kwargs)


def cleanup_old_data_task(max_age_days: int = 30) -> dict:
    """Clean up old background tasks and audit logs."""
    from cms.background import cleanup_old_tasks

    app = _get_app()
    with app.app_context():
        deleted_tasks = cleanup_old_tasks(max_age_hours=max_age_days * 24)

        from cms.models import db, AuditLog
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=max_age_days
        )
        try:
            deleted_logs = AuditLog.query.filter(AuditLog.created_at < cutoff).delete(
                synchronize_session="fetch"
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            deleted_logs = 0

        logger.info(
            "Cleanup: removed %d background tasks, %d audit logs",
            deleted_tasks,
            deleted_logs,
        )
        return {"deleted_tasks": deleted_tasks, "deleted_logs": deleted_logs}
