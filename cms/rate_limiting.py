"""
Rate limiting utilities for API endpoints and external platform calls.
Rate limit state is persisted to a JSON file so it survives restarts.
"""

import json
import os
import time
import threading
from datetime import datetime, timedelta
from functools import wraps

import flask

logger = __import__('logging').getLogger(__name__)

_PERSIST_PATH = os.environ.get(
    'RATE_LIMIT_PERSIST_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'rate_limits.json')
)
_PERSIST_LOCK = threading.Lock()
_last_save: float = 0
_PERSIST_INTERVAL = 5.0  # seconds between disk writes


def _maybe_save():
    """Save to disk at most every `_PERSIST_INTERVAL` seconds."""
    global _last_save
    now = time.time()
    if now - _last_save >= _PERSIST_INTERVAL:
        _save_persisted(_api_rate_limits, _platform_rate_limits)
        _last_save = now


def _load_persisted() -> tuple[dict, dict]:
    """Load persisted rate limits from JSON file."""
    with _PERSIST_LOCK:
        try:
            if os.path.exists(_PERSIST_PATH):
                with open(_PERSIST_PATH) as f:
                    data = json.load(f)
                return data.get('api', {}), data.get('platform', {})
        except Exception as e:
            logger.debug(f"Failed to load rate limits: {e}")
    return {}, {}


def _save_persisted(api: dict, platform: dict):
    """Save rate limits to JSON file."""
    with _PERSIST_LOCK:
        try:
            with open(_PERSIST_PATH, 'w') as f:
                json.dump({'api': api, 'platform': platform, '_saved_at': time.time()}, f)
        except Exception as e:
            logger.debug(f"Failed to persist rate limits: {e}")


# ---------------------------------------------------------------------------
# API Rate Limiter — per-IP sliding window for Flask routes
# ---------------------------------------------------------------------------

_api_rate_limits: dict = {}
_api_lock = threading.Lock()

DEFAULT_RATE_LIMIT = (100, 60)   # 100 requests / 60 seconds
STRICT_RATE_LIMIT = (30, 60)     # 30 requests / 60 seconds
GLOBAL_LIMIT = (300, 60)         # 300 requests / 60 seconds per IP (all endpoints combined)


def load_rate_limits():
    """Load persisted rate limits from disk. Called on startup."""
    api_data, platform_data = _load_persisted()
    with _api_lock:
        _api_rate_limits.clear()
        _api_rate_limits.update(api_data)
    with _platform_lock:
        _platform_rate_limits.clear()
        _platform_rate_limits.update(platform_data)
    logger.info(f"Restored {len(api_data)} API + {len(platform_data)} platform rate limits")


def rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix='default'):
    max_requests, window_seconds = limit
    global_max, global_window = GLOBAL_LIMIT

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            with _api_lock:
                now = time.time()
                client_ip = flask.request.remote_addr or '127.0.0.1'

                # Global per-IP check (all endpoints combined)
                global_key = f"_global:{client_ip}"
                if global_key not in _api_rate_limits:
                    _api_rate_limits[global_key] = {'count': 0, 'window_start': now}
                gdata = _api_rate_limits[global_key]
                if now - gdata['window_start'] > global_window:
                    gdata['count'] = 0
                    gdata['window_start'] = now
                if gdata['count'] >= global_max:
                    retry_after = int(global_window - (now - gdata['window_start']))
                    return flask.jsonify({
                        'error': 'Rate limit exceeded',
                        'limit': global_max,
                        'window_seconds': global_window,
                        'retry_after': max(retry_after, 1)
                    }), 429, {'Retry-After': str(max(retry_after, 1))}
                gdata['count'] += 1

                # Per-endpoint per-IP check
                key = f"{key_prefix}:{client_ip}"

                if key not in _api_rate_limits:
                    _api_rate_limits[key] = {'count': 0, 'window_start': now}

                rate_data = _api_rate_limits[key]

                if now - rate_data['window_start'] > window_seconds:
                    rate_data['count'] = 0
                    rate_data['window_start'] = now

                if rate_data['count'] >= max_requests:
                    retry_after = int(window_seconds - (now - rate_data['window_start']))
                    return flask.jsonify({
                        'error': 'Rate limit exceeded',
                        'limit': max_requests,
                        'window_seconds': window_seconds,
                        'retry_after': max(retry_after, 1)
                    }), 429, {'Retry-After': str(max(retry_after, 1))}

                rate_data['count'] += 1
                _maybe_save()

            response = f(*args, **kwargs)

            if hasattr(response, 'headers'):
                response.headers['X-RateLimit-Limit'] = str(max_requests)
                response.headers['X-RateLimit-Remaining'] = str(max(0, max_requests - rate_data['count']))
                response.headers['X-RateLimit-Window'] = str(window_seconds)

            return response
        return decorated_function
    return decorator


def get_api_rate_limit_status() -> list:
    with _api_lock:
        now = time.time()
        status = []
        for key, data in _api_rate_limits.items():
            elapsed = now - data['window_start']
            remaining_window = max(0, 60 - elapsed)
            if data['count'] > 0 or remaining_window > 0:
                status.append({
                    'key': key,
                    'requests': data['count'],
                    'remaining_window': int(remaining_window)
                })
        return status


# ---------------------------------------------------------------------------
# Platform/Site Rate Limiter — per-site cooldown
# ---------------------------------------------------------------------------

_platform_rate_limits: dict = {}
_platform_lock = threading.Lock()

RATE_LIMIT_STATUS_CODES = {429, 403, 503}
RETRY_MAX_ATTEMPTS = 2
RETRY_BASE_DELAY = 1


def is_rate_limited(site_name: str):
    with _platform_lock:
        if site_name in _platform_rate_limits:
            limit_data = _platform_rate_limits[site_name]
            if datetime.now() < limit_data['reset_at']:
                return True, limit_data
        return False, None


def set_rate_limited(site_name: str, retry_after: int = 60):
    with _platform_lock:
        _platform_rate_limits[site_name] = {
            'limited_at': datetime.now(),
            'reset_at': datetime.now() + timedelta(seconds=retry_after),
            'count': _platform_rate_limits.get(site_name, {}).get('count', 0) + 1
        }
        _maybe_save()


def get_rate_limit_status() -> list:
    with _platform_lock:
        now = datetime.now()
        limited = []
        for site, data in _platform_rate_limits.items():
            if now < data['reset_at']:
                remaining = (data['reset_at'] - now).seconds
                limited.append({'site': site, 'remaining_seconds': remaining})
        return limited


def cleanup_rate_limits(max_age_seconds: int = 3600):
    now = datetime.now()
    with _platform_lock:
        stale = [k for k, v in _platform_rate_limits.items()
                 if (now - v['limited_at']).total_seconds() > max_age_seconds]
        for k in stale:
            del _platform_rate_limits[k]
    with _api_lock:
        stale_api = [k for k, v in _api_rate_limits.items()
                     if time.time() - v['window_start'] > max_age_seconds]
        for k in stale_api:
            del _api_rate_limits[k]
    if stale or stale_api:
        _save_persisted(_api_rate_limits, _platform_rate_limits)


# Load persisted rate limits on import (must be after all definitions)
load_rate_limits()
