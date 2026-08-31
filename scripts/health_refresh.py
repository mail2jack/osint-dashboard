#!/usr/bin/env python3
"""Run one bounded full-health refresh outside the web request worker."""

import fcntl
import json
import logging
import signal
import time
from datetime import datetime, timezone

from app import _set_cli_tenant_context, app
from cms.health_utils import check_external_services
from cms.models import Setting, db

logger = logging.getLogger(__name__)
LOCK_PATH = "/tmp/osint-health-refresh.lock"
REFRESH_TIMEOUT_SECONDS = 75


class RefreshTimeout(Exception):
    """Raised when the full refresh exceeds its process budget."""


def _timeout_handler(signum, frame):
    raise RefreshTimeout("refresh exceeded 75 second budget")


def _store_snapshot(snapshot: dict) -> None:
    Setting.set(
        "health_snapshot",
        json.dumps(snapshot, separators=(",", ":")),
        category="system",
        description="Last full health snapshot and monotonic timings",
    )
    db.session.commit()


def main() -> int:
    with open(LOCK_PATH, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning("Health refresh already running; flock contention")
            return 0

        timings: dict[str, float] = {}
        started = time.monotonic()
        previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, REFRESH_TIMEOUT_SECONDS)
        try:
            with app.app_context():
                _set_cli_tenant_context()
                services = check_external_services(timings=timings)
                snapshot = {
                    "services": services,
                    "timings_ms": timings,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
                snapshot["refresh_status"] = "success"
                snapshot["refresh_error"] = None
                _store_snapshot(snapshot)
            return 0
        except RefreshTimeout as exc:
            logger.error("Full health refresh timed out after %ss", REFRESH_TIMEOUT_SECONDS)
            with app.app_context():
                db.session.rollback()
                _store_snapshot({
                    "services": {},
                    "timings_ms": timings,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "refresh_status": "timeout",
                    "refresh_error": type(exc).__name__,
                })
            return 1
        except Exception:
            logger.exception("Full health refresh failed")
            with app.app_context():
                db.session.rollback()
                _store_snapshot({
                    "services": {},
                    "timings_ms": timings,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "refresh_status": "failed",
                    "refresh_error": "unexpected_error",
                })
            return 1
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
