from datetime import datetime, timedelta

from cms.constants import CACHE_TTL_HOURS

result_cache = {}


def get_cache_key(search_type, query, category="all"):
    return f"{search_type}:{query}:{category}".lower()


def get_cached_result(search_type, value, category="all"):
    key = get_cache_key(search_type, value, category)
    if key in result_cache:
        cached = result_cache[key]
        if datetime.now() - cached["timestamp"] < timedelta(hours=CACHE_TTL_HOURS):
            cached["from_cache"] = True
            return cached["result"]
        else:
            del result_cache[key]
    return None


def set_cached_result(search_type, value, data, category="all"):
    key = get_cache_key(search_type, value, category)
    result_cache[key] = {"result": data, "timestamp": datetime.now()}


def clear_cache():
    global result_cache
    result_cache = {}
    return len(result_cache)


def get_cache_info():
    now = datetime.now()
    valid = 0
    expired = 0
    for cached in result_cache.values():
        if now - cached["timestamp"] < timedelta(hours=CACHE_TTL_HOURS):
            valid += 1
        else:
            expired += 1
    return {"total": len(result_cache), "valid": valid, "expired": expired}


__all__ = [
    "result_cache",
    "get_cache_key",
    "get_cached_result",
    "set_cached_result",
    "clear_cache",
    "get_cache_info",
]
