"""
Simple in-memory cache for Setting.get() queries.
Avoids hitting the database on every request for frequently-read settings
(spiderfoot_health, theme_style, etc.). Cache TTL is 60 seconds.
"""

import time
from threading import Lock
from typing import Any

_cache: dict[str, tuple[float, Any]] = {}
_lock = Lock()
TTL = 60.0


def cached_setting_get(key: str, default: Any = None) -> Any:
    """Like Setting.get() but cached for TTL seconds."""
    now = time.time()

    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < TTL:
            return cached[1]

    from .models import Setting
    value = Setting.get(key, default)

    with _lock:
        _cache[key] = (now, value)

    return value


def invalidate_setting(key: str) -> None:
    """Clear a cached setting (call after Setting.set())."""
    with _lock:
        _cache.pop(key, None)


def invalidate_all() -> None:
    """Clear entire setting cache."""
    with _lock:
        _cache.clear()
