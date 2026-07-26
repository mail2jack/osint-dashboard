"""
API Key authentication for external tool access.

Provides:
- ``api_key_required`` — authenticate via X-API-Key header
- ``require_scope`` — enforce a minimum API key scope (read / write / admin)
"""

import logging
from datetime import datetime, timezone
from functools import wraps

from flask import request, jsonify, g
from flask_login import current_user, login_user

from .models import db, ApiKey, User

logger = logging.getLogger(__name__)


def api_key_required(f):
    """Decorator: require valid X-API-Key header.

    Sets g.api_user_id / g.api_key_name / g.api_key_scopes on success,
    and logs the user in via Flask-Login so that @login_required deeper
    in the chain works.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip if already authenticated via session
        if current_user and current_user.is_authenticated:
            return f(*args, **kwargs)

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            logger.debug("No X-API-Key header from %s", request.remote_addr)
            return jsonify(
                {"error": "API key required. Provide X-API-Key header."}
            ), 401

        prefix = api_key[:8] if len(api_key) >= 8 else api_key
        key_record = ApiKey.query.filter_by(key_prefix=prefix, is_active=True).first()

        if not key_record:
            logger.debug("No active key for provided prefix")
            return jsonify({"error": "Invalid API key"}), 401

        if not key_record.verify_key(api_key):
            logger.debug("API key hash mismatch")
            return jsonify({"error": "Invalid API key"}), 401

        key_record.last_used_at = datetime.now(timezone.utc)
        db.session.commit()

        g.api_user_id = key_record.user_id
        g.api_key_name = key_record.name
        g.api_key_scopes = key_record.scopes or ["read"]

        # Log in via Flask-Login so @login_required wrappers pass
        user = db.session.get(User, key_record.user_id)
        if user:
            logger.debug("Login user via API key (active=%s)", user.is_active)
            login_user(user)

        return f(*args, **kwargs)

    return decorated_function


def require_scope(min_scope: str):
    """Decorator: enforce a minimum API key scope.

    Usage::

        @api_key_required
        @require_scope("write")
        def my_endpoint():

    Scope hierarchy: read < write < admin.
    A key with ``admin`` scope passes any ``min_scope``.
    Session-authenticated users (no API key) pass all scope checks.
    """

    _HIERARCHY = {"read": 0, "write": 1, "admin": 2}

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if min_scope not in _HIERARCHY:
                logger.warning("Unknown scope '%s' in require_scope", min_scope)
                return jsonify({"error": "Internal server error"}), 500

            # Session-authenticated users are not subject to API key scopes
            scopes = g.get("api_key_scopes")
            if scopes is None:
                return f(*args, **kwargs)

            required_level = _HIERARCHY[min_scope]
            max_level = max(_HIERARCHY.get(s, -1) for s in scopes)
            if max_level < required_level:
                return jsonify(
                    {
                        "error": "Insufficient API key scope",
                        "required": min_scope,
                        "actual": scopes,
                    }
                ), 403

            return f(*args, **kwargs)

        return wrapper

    return decorator
