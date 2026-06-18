"""Reusable decorators for routes: audit logging, response envelop, etc."""

import functools
import logging
from typing import Any, Callable

from flask import request
from flask_login import current_user

from .models import AuditLog, db

logger = logging.getLogger(__name__)


def audit_log(
    action: str,
    entity_type: str | None = None,
    entity_id_arg: str | None = None,
) -> Callable:
    """Decorator that logs an AuditLog entry after the decorated view returns.

    Usage::

        @audit_log("case.created", entity_type="case", entity_id_arg="case_id")
        def create_case(case_id):
            ...

    ``entity_id_arg`` is the name of a kwarg passed to the view (e.g. the URL
    variable).  If omitted, no entity_id is logged.

    The decorator reads ``current_user`` and ``request.remote_addr`` at
    decoration time to populate the log.
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = f(*args, **kwargs)
            try:
                entity_id = None
                if entity_id_arg and entity_id_arg in kwargs:
                    entity_id = str(kwargs[entity_id_arg])
                log = AuditLog(
                    action=action,
                    entity_type=entity_type or "",
                    entity_id=entity_id or "",
                    user_id=str(current_user.id) if current_user else "",
                    ip_address=request.remote_addr or "",
                    user_agent=request.user_agent.string or "",
                )
                db.session.add(log)
                db.session.commit()
            except Exception:
                logger.exception("Failed to write audit log for %s", action)
                db.session.rollback()
            return result

        return wrapper

    return decorator
