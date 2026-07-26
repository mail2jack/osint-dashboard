"""
Rate limiting utilities for API endpoints and external platform calls.
Rate limit state is persisted to the database via Setting model
so it survives restarts.

Supports per-tenant rate limits (checked in addition to per-IP limits)
using tier defaults stored in PlatformSetting("rate_limit_tier_defaults")
with optional per-tenant overrides in PlatformSetting("rate_limit_overrides").
"""

import logging
import time
import threading
from datetime import datetime, timedelta
from functools import wraps

import flask

logger = logging.getLogger(__name__)

_SETTING_KEY_API = "rate_limits_api"
_SETTING_KEY_PLATFORM = "rate_limits_platform"
_SETTING_KEY_TIER = "rate_limit_tier_defaults"
_SETTING_KEY_OVERRIDES = "rate_limit_overrides"
_LOADED = False

# ---------------------------------------------------------------------------
# API Rate Limiter — per-IP sliding window for Flask routes
# ---------------------------------------------------------------------------

_api_rate_limits: dict = {}
_api_lock = threading.Lock()

DEFAULT_RATE_LIMIT = (100, 60)  # 100 requests / 60 seconds
STRICT_RATE_LIMIT = (30, 60)  # 30 requests / 60 seconds
GLOBAL_LIMIT = (300, 60)  # 300 requests / 60 seconds per IP (all endpoints combined)

# ---------------------------------------------------------------------------
# Per-tenant rate limit counters — separate from per-IP limits
# ---------------------------------------------------------------------------

_tenant_api_counters: dict = {}
_tenant_lock = threading.Lock()

DEFAULT_TENANT_RATE_LIMIT = 200  # requests/min fallback if no config found


def load_rate_limits():
    """Load persisted rate limits from the database. Called on startup."""
    global _api_rate_limits, _platform_rate_limits, _LOADED
    try:
        from .models import Setting

        api_data = Setting.get(_SETTING_KEY_API, {}) or {}
        platform_data = Setting.get(_SETTING_KEY_PLATFORM, {}) or {}
    except Exception:
        api_data, platform_data = {}, {}

    with _api_lock:
        _api_rate_limits.clear()
        _api_rate_limits.update(_prune_stale_api(api_data))
    with _platform_lock:
        _platform_rate_limits.clear()
        _platform_rate_limits.update(_prune_stale_platform(platform_data))
    _LOADED = True
    logger.info(
        "Restored %d API + %d platform rate limits from DB",
        len(_api_rate_limits),
        len(_platform_rate_limits),
    )


def _save_now():
    """Synchronously persist current state to DB. Caller must NOT hold api_lock or platform_lock."""
    try:
        from .models import Setting

        with _api_lock:
            api_snapshot = dict(_api_rate_limits)
        with _platform_lock:
            platform_snapshot = dict(_platform_rate_limits)
        Setting.set(_SETTING_KEY_API, api_snapshot, category="system", encrypt=False)
        Setting.set(
            _SETTING_KEY_PLATFORM, platform_snapshot, category="system", encrypt=False
        )
    except Exception as e:
        logger.debug("Failed to persist rate limits: %s", e)


def _prune_stale_api(data: dict) -> dict:
    """Remove API rate limit entries older than their window."""
    now = time.time()
    return {k: v for k, v in data.items() if now - v.get("window_start", 0) < 120}


def _prune_stale_platform(data: dict) -> dict:
    """Remove platform rate limit entries that have expired."""
    now = datetime.now()
    result = {}
    for k, v in data.items():
        reset_at = v.get("reset_at")
        if isinstance(reset_at, str):
            try:
                reset_at = datetime.fromisoformat(reset_at)
            except Exception:
                continue
        if isinstance(reset_at, datetime) and now < reset_at:
            result[k] = v
    return result


def _normalize_for_db(data: dict) -> dict:
    """Convert datetime objs to ISO strings before DB storage."""
    result = {}
    for k, v in data.items():
        entry = dict(v)
        if isinstance(entry.get("reset_at"), datetime):
            entry["reset_at"] = entry["reset_at"].isoformat()
        if isinstance(entry.get("limited_at"), datetime):
            entry["limited_at"] = entry["limited_at"].isoformat()
        result[k] = entry
    return result


_last_save: float = 0
_PERSIST_INTERVAL = 10.0  # seconds between DB writes


def _maybe_save_api():
    """Save API rate limits to DB at most every `_PERSIST_INTERVAL` seconds."""
    global _last_save
    now = time.time()
    if now - _last_save < _PERSIST_INTERVAL:
        return
    _last_save = now
    _save_now()


def rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="default"):
    max_requests, window_seconds = limit
    global_max, global_window = GLOBAL_LIMIT

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            with _api_lock:
                now = time.time()
                client_ip = flask.request.remote_addr or "127.0.0.1"

                # Global per-IP check (all endpoints combined)
                global_key = f"_global:{client_ip}"
                if global_key not in _api_rate_limits:
                    _api_rate_limits[global_key] = {"count": 0, "window_start": now}
                gdata = _api_rate_limits[global_key]
                if now - gdata["window_start"] > global_window:
                    gdata["count"] = 0
                    gdata["window_start"] = now
                if gdata["count"] >= global_max:
                    retry_after = int(global_window - (now - gdata["window_start"]))
                    return (
                        flask.jsonify(
                            {
                                "error": "Rate limit exceeded",
                                "limit": global_max,
                                "window_seconds": global_window,
                                "retry_after": max(retry_after, 1),
                            }
                        ),
                        429,
                        {"Retry-After": str(max(retry_after, 1))},
                    )
                gdata["count"] += 1

                # Per-endpoint per-IP check
                key = f"{key_prefix}:{client_ip}"

                if key not in _api_rate_limits:
                    _api_rate_limits[key] = {"count": 0, "window_start": now}

                rate_data = _api_rate_limits[key]

                if now - rate_data["window_start"] > window_seconds:
                    rate_data["count"] = 0
                    rate_data["window_start"] = now

                if rate_data["count"] >= max_requests:
                    retry_after = int(
                        window_seconds - (now - rate_data["window_start"])
                    )
                    return (
                        flask.jsonify(
                            {
                                "error": "Rate limit exceeded",
                                "limit": max_requests,
                                "window_seconds": window_seconds,
                                "retry_after": max(retry_after, 1),
                            }
                        ),
                        429,
                        {"Retry-After": str(max(retry_after, 1))},
                    )

                rate_data["count"] += 1

            # Per-user limit check (only for authenticated users)
            try:
                from flask_login import current_user as _cu

                if _cu and _cu.is_authenticated:
                    user_key = f"user:{_cu.id}:{key_prefix}"
                    if user_key not in _api_rate_limits:
                        _api_rate_limits[user_key] = {
                            "count": 0,
                            "window_start": now,
                        }
                    udata = _api_rate_limits[user_key]
                    if now - udata["window_start"] > window_seconds:
                        udata["count"] = 0
                        udata["window_start"] = now
                    if udata["count"] >= max_requests:
                        retry_after = int(
                            window_seconds - (now - udata["window_start"])
                        )
                        return (
                            flask.jsonify(
                                {
                                    "error": "Rate limit exceeded (per-user)",
                                    "limit": max_requests,
                                    "window_seconds": window_seconds,
                                    "retry_after": max(retry_after, 1),
                                }
                            ),
                            429,
                            {"Retry-After": str(max(retry_after, 1))},
                        )
                    udata["count"] += 1
            except Exception:
                logger.debug("Rate limit counter skipped: %s", exc_info=True)

            # Per-tenant limit check (only for authenticated users)
            try:
                from flask_login import current_user as _cu

                if _cu and _cu.is_authenticated:
                    tid = _cu.tenant_id
                    tkey = f"tenant:{tid}:{key_prefix}"
                    tlimit = get_tenant_rate_limit(tid)

                    with _tenant_lock:
                        tnow = time.time()
                        if tkey not in _tenant_api_counters:
                            _tenant_api_counters[tkey] = {
                                "count": 0,
                                "window_start": tnow,
                            }
                        tdata = _tenant_api_counters[tkey]
                        if tnow - tdata["window_start"] > 60:
                            tdata["count"] = 0
                            tdata["window_start"] = tnow
                        if tdata["count"] >= tlimit:
                            retry_after = int(60 - (tnow - tdata["window_start"]))
                            return (
                                flask.jsonify(
                                    {
                                        "error": "Tenant rate limit exceeded",
                                        "limit": tlimit,
                                        "window_seconds": 60,
                                        "retry_after": max(retry_after, 1),
                                    }
                                ),
                                429,
                                {"Retry-After": str(max(retry_after, 1))},
                            )
                        tdata["count"] += 1
            except Exception:
                logger.debug("Tenant rate limit counter skipped", exc_info=True)

            response = f(*args, **kwargs)

            if hasattr(response, "headers"):
                response.headers["X-RateLimit-Limit"] = str(max_requests)
                response.headers["X-RateLimit-Remaining"] = str(
                    max(0, max_requests - rate_data["count"])
                )
                response.headers["X-RateLimit-Window"] = str(window_seconds)

            _maybe_save_api()

            return response

        return decorated_function

    return decorator


# ---------------------------------------------------------------------------
# Per-tenant rate limit helpers
# ---------------------------------------------------------------------------


def get_tier_rate_limit(tier: str) -> int:
    """Return the default rate limit (requests/min) for a given tier."""
    try:
        from .models import Setting as _Setting

        raw = _Setting.get(_SETTING_KEY_TIER)
        if raw:
            import json

            config = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(config, dict) and tier in config:
                return int(config[tier])
    except Exception:
        pass
    return DEFAULT_TENANT_RATE_LIMIT


def get_tenant_rate_limit(tenant_id: str, tier: str | None = None) -> int:
    """Return effective per-tenant rate limit (requests/min).

    Priority: per-tenant override > tier default > fallback.
    """
    try:
        from .models import Setting as _Setting

        raw = _Setting.get(_SETTING_KEY_OVERRIDES)
        if raw:
            import json

            overrides = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(overrides, dict) and tenant_id in overrides:
                return int(overrides[tenant_id])
    except Exception:
        pass

    if tier:
        return get_tier_rate_limit(tier)
    try:
        from .models import Tenant

        tenant = Tenant.query.get(tenant_id)
        if tenant:
            return get_tier_rate_limit(tenant.tier)
    except Exception:
        pass
    return DEFAULT_TENANT_RATE_LIMIT


def get_tenant_api_rate_status() -> list:
    """Return current per-tenant rate limit counters for monitoring."""
    with _tenant_lock:
        now = time.time()
        return [
            {
                "tenant_key": key,
                "count": data["count"],
                "window_start": data["window_start"],
                "remaining": max(0, 60 - (now - data["window_start"])),
            }
            for key, data in _tenant_api_counters.items()
            if data["count"] > 0
        ]


def get_api_rate_limit_status() -> list:
    with _api_lock:
        now = time.time()
        status = []
        for key, data in _api_rate_limits.items():
            elapsed = now - data["window_start"]
            remaining_window = max(0, 60 - elapsed)
            if data["count"] > 0 or remaining_window > 0:
                status.append(
                    {
                        "key": key,
                        "requests": data["count"],
                        "remaining_window": int(remaining_window),
                    }
                )
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
            reset_at = limit_data.get("reset_at")
            if isinstance(reset_at, str):
                try:
                    reset_at = datetime.fromisoformat(reset_at)
                    limit_data["reset_at"] = reset_at
                except Exception:
                    reset_at = None
            if isinstance(reset_at, datetime) and datetime.now() < reset_at:
                return True, limit_data
        return False, None


def set_rate_limited(site_name: str, retry_after: int = 60):
    with _platform_lock:
        _platform_rate_limits[site_name] = {
            "limited_at": datetime.now(),
            "reset_at": datetime.now() + timedelta(seconds=retry_after),
            "count": _platform_rate_limits.get(site_name, {}).get("count", 0) + 1,
        }
    _save_now()


def rate_limit_after_n(
    site_name: str,
    max_attempts: int = 3,
    retry_after: int = 15,
    reset_after: int = 300,
):
    """Increment failure count; set rate limit only after max_attempts failures.

    Counter resets if last attempt was more than ``reset_after`` seconds ago.
    Returns the current attempt count (1-based).
    """
    with _platform_lock:
        now = datetime.now()
        current = _platform_rate_limits.get(site_name, {})
        last_attempt = current.get("limited_at")
        if isinstance(last_attempt, str):
            try:
                last_attempt = datetime.fromisoformat(last_attempt)
            except Exception:
                last_attempt = None

        count = current.get("count", 0)

        # Reset if last attempt was too long ago
        if (
            isinstance(last_attempt, datetime)
            and (now - last_attempt).total_seconds() > reset_after
        ):
            count = 0

        count += 1

        if count >= max_attempts:
            _platform_rate_limits[site_name] = {
                "limited_at": now,
                "reset_at": now + timedelta(seconds=retry_after),
                "count": count,
            }
        else:
            _platform_rate_limits[site_name] = {
                "limited_at": now,
                "count": count,
            }

    _save_now()
    return count


def get_rate_limit_status() -> list:
    with _platform_lock:
        now = datetime.now()
        limited = []
        for site, data in _platform_rate_limits.items():
            reset_at = data.get("reset_at")
            if isinstance(reset_at, str):
                try:
                    reset_at = datetime.fromisoformat(reset_at)
                except Exception:
                    continue
            if isinstance(reset_at, datetime) and now < reset_at:
                remaining = (reset_at - now).seconds
                limited.append({"site": site, "remaining_seconds": remaining})
        return limited


def cleanup_rate_limits(max_age_seconds: int = 3600):
    now = datetime.now()
    with _platform_lock:
        stale = [
            k
            for k, v in _platform_rate_limits.items()
            if isinstance(v.get("limited_at"), str)
            or (
                isinstance(v.get("limited_at"), datetime)
                and (now - v["limited_at"]).total_seconds() > max_age_seconds
            )
        ]
        for k in stale:
            del _platform_rate_limits[k]
    with _api_lock:
        stale_api = [
            k
            for k, v in _api_rate_limits.items()
            if time.time() - v["window_start"] > max_age_seconds
        ]
        for k in stale_api:
            del _api_rate_limits[k]
    if stale or stale_api:
        _save_now()


# Module-level: ensure we restore from DB before first request
if not _LOADED:
    load_rate_limits()
