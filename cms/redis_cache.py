"""
Redis cache wrapper for OSINT results.

Falls back to filesystem cache when Redis is unavailable.
Set REDIS_URL env var to enable Redis (e.g. redis://redis:6379/0).
"""

import hashlib
import logging
import os
import threading

logger = logging.getLogger(__name__)

_redis_client = None
_redis_lock = threading.Lock()
_default_timeout = 300

_fs_cache = None
_fs_lock = threading.Lock()


def _get_redis():
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL")
        if not url:
            return None
        with _redis_lock:
            if _redis_client is None:
                try:
                    import redis as _redis_mod

                    _redis_client = _redis_mod.from_url(url, decode_responses=True)
                    _redis_client.ping()
                    logger.info("Redis cache connected: %s", url)
                except Exception:
                    logger.warning(
                        "Redis unavailable, falling back to filesystem cache"
                    )
                    _redis_client = False
    return _redis_client if _redis_client else None


def _get_fs_cache():
    global _fs_cache
    if _fs_cache is None:
        with _fs_lock:
            if _fs_cache is None:
                from cachelib.file import FileSystemCache

                cache_dir = os.environ.get(
                    "OSINT_CACHE_DIR",
                    os.path.join(os.path.dirname(__file__), "..", "osint_cache"),
                )
                _fs_cache = FileSystemCache(
                    cache_dir, threshold=1000, default_timeout=_default_timeout
                )
    return _fs_cache


def _make_key(tool: str, query: str) -> str:
    raw = f"{tool}:{query.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get(tool: str, query: str) -> dict | None:
    key = _make_key(tool, query)
    redis_ = _get_redis()
    if redis_:
        try:
            import json

            val = redis_.get(f"osint:{key}")
            if val is not None:
                return json.loads(val)
        except Exception as e:
            logger.debug("Redis get error: %s", e)
            _reset_redis()
    try:
        return _get_fs_cache().get(key)
    except Exception as e:
        logger.debug("FS cache get error: %s", e)
        return None


def set(tool: str, query: str, data: dict, timeout: int = _default_timeout):
    key = _make_key(tool, query)
    redis_ = _get_redis()
    if redis_:
        try:
            import json

            redis_.setex(f"osint:{key}", timeout, json.dumps(data))
            return
        except Exception as e:
            logger.debug("Redis set error: %s", e)
            _reset_redis()
    try:
        _get_fs_cache().set(key, data, timeout=timeout)
    except Exception as e:
        logger.debug("FS cache set error: %s", e)


def _purge_osint_keys(redis_) -> None:
    """Delete only the ``osint:*`` keyspace via scan/unlink.

    Never ``flushall()``: Flask-Session (and RQ) may share the same Redis
    instance/database, and a full flush would silently drop every live
    session.  ``invalidate()`` must be scoped to the OSINT cache keys only.
    """
    batch = []
    for key in redis_.scan_iter(match="osint:*", count=500):
        batch.append(key)
        if len(batch) >= 100:
            redis_.unlink(*batch)
            batch.clear()
    if batch:
        redis_.unlink(*batch)


def invalidate(tool: str = None, query: str = None):
    redis_ = _get_redis()
    if redis_ and tool and query:
        try:
            redis_.delete(f"osint:{_make_key(tool, query)}")
            return
        except Exception:
            _reset_redis()
    if redis_:
        try:
            _purge_osint_keys(redis_)
            return
        except Exception:
            _reset_redis()
    try:
        _get_fs_cache().clear()
    except Exception as e:
        logger.debug("FS cache clear error: %s", e)


def get_status() -> dict:
    redis_ = _get_redis()
    if redis_:
        try:
            info = redis_.info("memory")
            dbsize = redis_.dbsize()
            return {
                "type": "redis",
                "connected": True,
                "used_memory": info.get("used_memory_human", "unknown"),
                "keys": dbsize,
                "default_timeout": _default_timeout,
            }
        except Exception:
            _reset_redis()
    try:
        cache = _get_fs_cache()
        return {
            "type": "filesystem",
            "directory": cache._cache_dir
            if hasattr(cache, "_cache_dir")
            else "unknown",
            "default_timeout": _default_timeout,
        }
    except Exception:
        return {"error": "Internal server error"}


def _reset_redis():
    global _redis_client
    _redis_client = None
