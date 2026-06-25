import json
import logging
import os
import time
from functools import lru_cache

from curl_cffi import requests as curl_requests
from cms.services.http_utils import jitter_sleep

from cms.constants import WHATSMYNAME_DATA_URL

logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "instance",
    "whatsmyname_cache.json",
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
        logger.warning("Failed to write WhatsMyName cache: %s", e)


_FIELD_MAP = {
    "uri_check": "check_uri",
    "uri_pretty": "uri_pretty",
    "e_code": "account_existence_code",
    "e_string": "account_existence_string",
    "m_string": "account_missing_string",
    "m_code": "account_missing_code",
    "post_body": "mBody",
    "known": "known_accounts",
    "cat": "category",
}


def _remap_entry(entry):
    """Map wmn-data.json fields to legacy field names used by check_whatsmyname_site."""
    mapped = {}
    for new_key, old_key in _FIELD_MAP.items():
        if new_key in entry:
            mapped[old_key] = entry[new_key]
    for key in ("name", "headers", "valid"):
        if key in entry:
            mapped[key] = entry[key]
    mbody = mapped.get("mBody", "")
    mapped["mCode"] = "POST" if mbody else "GET"
    # wmn-data.json uses {account} template var; legacy code expects {userName}
    for field in ("check_uri", "mBody"):
        val = mapped.get(field)
        if val and "{account}" in str(val):
            mapped[field] = str(val).replace("{account}", "{userName}")
    return mapped


@lru_cache(maxsize=1)
def get_whatsmyname_sites():
    cached = _load_cached()
    if cached is not None:
        return cached

    try:
        jitter_sleep(domain_hint=WHATSMYNAME_DATA_URL)
        response = curl_requests.get(WHATSMYNAME_DATA_URL, timeout=30)
        if response.status_code == 200:
            data = response.json()
            raw_sites = data if isinstance(data, list) else data.get("sites", [])
            sites = {}
            for entry in raw_sites:
                if entry.get("valid") is False:
                    continue
                name = entry.get("name", "").strip()
                if not name:
                    continue
                sites[name] = _remap_entry(entry)
            if sites:
                _save_cache(sites)
            return sites
    except Exception as e:
        logger.error(
            f"Failed to fetch WhatsMyName sites ({type(e).__name__}): {e}",
            exc_info=True,
        )
    return {}


__all__ = ["get_whatsmyname_sites"]
