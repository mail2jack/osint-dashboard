"""
Feature flags — enables/disables individual OSINT tools via Settings.
When a tool is disabled, its API endpoint returns 503 with a clear message.
"""

import logging
from functools import wraps

from flask import jsonify

logger = logging.getLogger(__name__)


def is_tool_enabled(tool_name: str) -> bool:
    """Check if a tool is enabled via Setting. Falls back to default (enabled)."""
    from .models import Setting
    setting_key = f'feature_{tool_name}'
    stored = Setting.get(setting_key)
    if stored is None:
        return True
    return stored.lower() in ('1', 'true', 'yes')


def tool_enabled(tool_name: str):
    """Decorator: return 503 if the OSINT tool is disabled."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not is_tool_enabled(tool_name):
                return jsonify({
                    'error': f'Tool "{tool_name}" is disabled',
                    'tool': tool_name,
                    'enabled': False,
                }), 503
            return f(*args, **kwargs)
        return decorated_function
    return decorator
