import json
import logging
import os
import time
from functools import lru_cache

from cms.services.http_utils import jittered_get

from cms.constants import SHERLOCK_DATA_URL

logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "instance", "sherlock_cache.json"
)
_CACHE_TTL = 86400  # 24 hours


def _load_cached():
    try:
        with open(_CACHE_FILE) as f:
            entry = json.load(f)
        if time.time() - entry.get("ts", 0) < _CACHE_TTL:
            return entry.get("data")
    except Exception:
        pass
    return None


def _save_cache(data):
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception as e:
        logger.warning("Failed to write Sherlock cache: %s", e)


@lru_cache(maxsize=1)
def get_sherlock_sites():
    cached = _load_cached()
    if cached is not None:
        return cached

    try:
        response = jittered_get(SHERLOCK_DATA_URL, timeout=30)
        if response.status_code == 200:
            data = response.json()
            data.pop("$schema", None)
            if data:
                _save_cache(data)
            return data
    except Exception as e:
        logger.error(
            f"Failed to fetch Sherlock sites ({type(e).__name__}): {e}", exc_info=True
        )
    return {}


__all__ = ["get_sherlock_sites"]
