"""
API Key authentication for external tool access.
"""

import logging
from functools import wraps

from flask import request, jsonify, g
from flask_login import current_user

from .models import db, ApiKey

logger = logging.getLogger(__name__)


def api_key_required(f):
    """Decorator: require valid X-API-Key header. Sets g.api_user on success."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip if already authenticated via session
        if current_user and current_user.is_authenticated:
            return f(*args, **kwargs)

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            return jsonify(
                {"error": "API key required. Provide X-API-Key header."}
            ), 401

        prefix = api_key[:8] if len(api_key) >= 8 else api_key
        key_record = ApiKey.query.filter_by(key_prefix=prefix, is_active=True).first()

        if not key_record or not key_record.verify_key(api_key):
            return jsonify({"error": "Invalid API key"}), 401

        key_record.last_used_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        db.session.commit()

        g.api_user_id = key_record.user_id
        g.api_key_name = key_record.name

        return f(*args, **kwargs)

    return decorated_function
