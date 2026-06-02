import threading
import time

from cms.logging_utils import logger

active_searches = {}
_searches_lock = threading.Lock()

_active_user_searches: dict = {}
_active_user_searches_lock = threading.Lock()
MAX_CONCURRENT_SEARCHES_PER_USER = 3

search_request_counts = {}

maigret_db = None
search_registry = {}


class SearchJob:
    def __init__(self, job_id):
        self.job_id = job_id
        self.cancelled = False
        self.progress_state = {"checked": 0, "found": 0, "current_site": "", "total": 0}
        self.result = None
        self.completed = False

    def cancel(self):
        self.cancelled = True

    def should_stop(self):
        return self.cancelled


def acquire_search_slot(user_id: str) -> bool:
    with _active_user_searches_lock:
        count = _active_user_searches.get(user_id, 0)
        if count >= MAX_CONCURRENT_SEARCHES_PER_USER:
            return False
        _active_user_searches[user_id] = count + 1
        return True


def release_search_slot(user_id: str):
    with _active_user_searches_lock:
        count = _active_user_searches.get(user_id, 0)
        if count <= 1:
            _active_user_searches.pop(user_id, None)
        else:
            _active_user_searches[user_id] = count - 1


def increment_request_count(search_type):
    key = f"{search_type}:{time.strftime('%Y%m%d%H')}"
    search_request_counts[key] = search_request_counts.get(key, 0) + 1
    return search_request_counts[key]


def get_maigret_database():
    global maigret_db
    if maigret_db is None:
        try:
            from maigret.sites import MaigretDatabase
            import os as _os

            maigret_db = MaigretDatabase()
            data_path = _os.path.join(
                _os.path.dirname(__import__("maigret", fromlist=[""]).__file__),
                "resources",
                "data.json",
            )
            maigret_db.load_from_path(data_path)
            logger.info(f"Loaded Maigret database with {len(maigret_db.sites)} sites")
        except Exception as e:
            logger.error(f"Failed to load Maigret database: {e}", exc_info=True)
            maigret_db = None
    return maigret_db


def get_maigret_sites_dict():
    db = get_maigret_database()
    if db:
        return db.sites_dict
    return {}


__all__ = [
    "SearchJob",
    "search_registry",
    "active_searches",
    "_searches_lock",
    "_active_user_searches",
    "_active_user_searches_lock",
    "MAX_CONCURRENT_SEARCHES_PER_USER",
    "search_request_counts",
    "acquire_search_slot",
    "release_search_slot",
    "increment_request_count",
    "get_maigret_database",
    "get_maigret_sites_dict",
]
