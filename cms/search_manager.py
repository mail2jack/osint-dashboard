"""
OSINT Search Manager
=====================
Manages background OSINT searches — DB-backed for multi-worker gunicorn.

Cancel events stay in-memory (per-worker) since threads share the same process.
All persistent state (status, results, timestamps) lives in the database.
"""

import threading
import logging
from datetime import datetime, timezone
from typing import Any

from .models import db, OsintSearch

logger = logging.getLogger(__name__)


class SearchManager:
    def __init__(self):
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create_search(
        self, case_id: str, search_id: str, query: str, subject_id: str = None
    ) -> threading.Event:
        """Create a new search: DB record + in-memory cancel event."""
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[search_id] = cancel_event

        row = OsintSearch(
            search_id=search_id,
            case_id=case_id,
            subject_id=subject_id,
            search_query=query,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.session.add(row)
        db.session.commit()
        return cancel_event

    def get_search(self, search_id: str) -> dict[str, Any] | None:
        """Get search info (DB row + in-memory cancel event)."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return None
        with self._lock:
            cancel_event = self._cancel_events.get(search_id)
        return {
            "cancel_event": cancel_event,
            "db_row": row,
            "search_id": row.search_id,
            "case_id": row.case_id,
            "query": row.search_query,
            "status": row.status,
            "results": row.results,
        }

    def set_results(self, search_id: str, results: Any) -> None:
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return
        row.results = results
        row.status = "completed"
        row.completed_at = datetime.now(timezone.utc)
        db.session.commit()

    def set_error(self, search_id: str, error: str) -> None:
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return
        row.status = "failed"
        row.error = error
        row.completed_at = datetime.now(timezone.utc)
        db.session.commit()

    def cancel_search(self, search_id: str) -> bool:
        """Cancel a running search — sets cancel event + DB state."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return False

        with self._lock:
            cancel_event = self._cancel_events.get(search_id)
        if cancel_event:
            cancel_event.set()

        if row.status == "running":
            row.status = "cancelled"
            row.cancelled_at = datetime.now(timezone.utc)
            db.session.commit()
        return True

    def cleanup(self, search_id: str) -> None:
        """Remove in-memory cancel event. DB row stays for history."""
        with self._lock:
            self._cancel_events.pop(search_id, None)

    def get_status(self, search_id: str) -> dict[str, Any] | None:
        """Get current search status from DB."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return None
        return row.get_status_dict()

    def is_cancelled(self, search_id: str) -> bool:
        """Check if search was cancelled (in-memory event + DB)."""
        with self._lock:
            cancel_event = self._cancel_events.get(search_id)
        if cancel_event and cancel_event.is_set():
            return True
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        return row.status == "cancelled" if row else False


search_manager = SearchManager()
