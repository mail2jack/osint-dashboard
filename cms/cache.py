"""
Simple filesystem-based cache for OSINT results and other data.
Uses cachelib (already installed via flask-session dependency).
"""

import hashlib
import json
import logging
import threading
import time

from cachelib.file import FileSystemCache

logger = logging.getLogger(__name__)

_cache: FileSystemCache = None
_cache_lock = threading.Lock()
_default_timeout = 300  # 5 minutes


def _get_cache() -> FileSystemCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                import os
                cache_dir = os.environ.get(
                    'OSINT_CACHE_DIR',
                    os.path.join(os.path.dirname(__file__), '..', 'osint_cache')
                )
                _cache = FileSystemCache(cache_dir, threshold=1000, default_timeout=_default_timeout)
    return _cache


def _make_key(tool: str, query: str) -> str:
    raw = f"{tool}:{query.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get(tool: str, query: str) -> dict | None:
    """Get cached result for (tool, query). Returns None if not cached or expired."""
    try:
        return _get_cache().get(_make_key(tool, query))
    except Exception as e:
        logger.debug(f"Cache get error: {e}")
        return None


def set(tool: str, query: str, data: dict, timeout: int = _default_timeout):
    """Cache result for (tool, query) with a TTL in seconds."""
    try:
        _get_cache().set(_make_key(tool, query), data, timeout=timeout)
    except Exception as e:
        logger.debug(f"Cache set error: {e}")


def invalidate(tool: str = None, query: str = None):
    """Invalidate cache entries. If both None, clears entire cache."""
    try:
        cache = _get_cache()
        if tool and query:
            cache.delete(_make_key(tool, query))
        elif tool:
            # Can't selectively clear by prefix with FileSystemCache; clear all
            cache.clear()
        else:
            cache.clear()
    except Exception as e:
        logger.debug(f"Cache invalidate error: {e}")


def get_status() -> dict:
    """Return cache statistics."""
    try:
        cache = _get_cache()
        return {
            'type': 'filesystem',
            'directory': cache._cache_dir if hasattr(cache, '_cache_dir') else 'unknown',
            'default_timeout': _default_timeout,
        }
    except Exception as e:
        return {'error': str(e)}
