#!/usr/bin/env python3
"""Run one bounded full-health refresh outside the web request worker."""

import fcntl
import json
import logging
import time
from datetime import datetime, timezone

from app import app
from cms.health_utils import check_external_services
from cms.models import Setting, db

logger = logging.getLogger(__name__)
LOCK_PATH = "/tmp/osint-health-refresh.lock"


def main() -> int:
    with open(LOCK_PATH, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Health refresh already running; exiting")
            return 0

        timings: dict[str, float] = {}
        started = time.monotonic()
        try:
            with app.app_context():
                from cms import _set_cli_tenant_context

                _set_cli_tenant_context()
                services = check_external_services(timings=timings)
                snapshot = {
                    "services": services,
                    "timings_ms": timings,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
                Setting.set(
                    "health_snapshot",
                    json.dumps(snapshot, separators=(",", ":")),
                    category="system",
                    description="Last full health snapshot and monotonic timings",
                )
                db.session.commit()
            return 0
        except Exception:
            logger.exception("Full health refresh failed")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
