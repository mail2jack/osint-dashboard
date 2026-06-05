"""
API Key authentication for external tool access.
"""

import logging
import sys
from functools import wraps

from flask import request, jsonify, g
from flask_login import current_user, login_user

from .models import db, ApiKey, User

logger = logging.getLogger(__name__)


def api_key_required(f):
    """Decorator: require valid X-API-Key header.

    Sets g.api_user_id / g.api_key_name on success, and logs the user in
    via Flask-Login so that @login_required deeper in the chain works.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip if already authenticated via session
        if current_user and current_user.is_authenticated:
            return f(*args, **kwargs)

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            print(
                f"[api_key_required] no X-API-Key header from {request.remote_addr}",
                file=sys.stderr,
                flush=True,
            )
            return jsonify(
                {"error": "API key required. Provide X-API-Key header."}
            ), 401

        prefix = api_key[:8] if len(api_key) >= 8 else api_key
        print(
            f"[api_key_required] checking prefix={prefix} key_len={len(api_key)}",
            file=sys.stderr,
            flush=True,
        )
        key_record = ApiKey.query.filter_by(key_prefix=prefix, is_active=True).first()

        if not key_record:
            print(
                f"[api_key_required] no active key for prefix={prefix}",
                file=sys.stderr,
                flush=True,
            )
            return jsonify({"error": "Invalid API key (not found)"}), 401

        if not key_record.verify_key(api_key):
            print(
                f"[api_key_required] hash mismatch for {key_record.name}",
                file=sys.stderr,
                flush=True,
            )
            return jsonify({"error": "Invalid API key (hash)"}), 401

        key_record.last_used_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        db.session.commit()

        g.api_user_id = key_record.user_id
        g.api_key_name = key_record.name

        # Log in via Flask-Login so @login_required wrappers pass
        user = db.session.get(User, key_record.user_id)
        if user:
            login_user(user)

        return f(*args, **kwargs)

    return decorated_function
