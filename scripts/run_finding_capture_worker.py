#!/usr/bin/env python3
"""Dedicated FEAT-1 capture-worker entry point.

This process is deliberately inert until a future sandboxed executor is
reviewed and installed.  It is separate from Gunicorn so web requests can
never run a browser.  The initial unit may safely be installed but is neither
enabled nor capable of falling back to an unsafe browser configuration.
"""

import argparse
import logging
import os
import signal
import sys
from threading import Event
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

from verify_finding_capture_sandbox import probe_sandbox, verify_sandbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="poll the queue until stopped")
    args = parser.parse_args(argv)

    if os.environ.get("FINDING_CAPTURE_WORKER_ENABLED") != "1":
        logger.info("Finding capture worker disabled; no queue job claimed")
        return 0

    chromium_path = (
        Path(os.environ["FINDING_CAPTURE_CHROMIUM_PATH"])
        if os.environ.get("FINDING_CAPTURE_CHROMIUM_PATH")
        else None
    )
    verified, reasons = verify_sandbox(
        unit_path=Path(__file__).resolve().parent.parent
        / "deploy"
        / "osint-finding-capture-worker.service",
        chromium_path=chromium_path,
    )
    if not verified or chromium_path is None:
        logger.error("Finding-capture sandbox verification failed: %s", "; ".join(reasons))
        return 78
    # The long-lived worker imports Flask once.  Each poll is read-only until
    # a queued job is found, so Chromium is never launched while idle.
    from app import app
    from cms.services.finding_capture_worker import has_queued_capture_job, process_one_capture_job

    # Importing the Flask app installs its own process-level signal hooks.
    # Reclaim SIGTERM here so a systemd restart can stop an idle polling worker
    # promptly instead of waiting for TimeoutStopSec and requiring SIGKILL.
    shutdown_requested = Event()

    def request_shutdown(_signum, _frame) -> None:
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    poll_seconds = int(os.environ.get("FINDING_CAPTURE_WORKER_POLL_SECONDS", "15"))
    if poll_seconds < 1:
        logger.error("Finding capture worker poll interval must be positive")
        return 78

    while not shutdown_requested.is_set():
        with app.app_context():
            queued = has_queued_capture_job()
        if queued:
            probed, reason = probe_sandbox(chromium_path)
            if not probed:
                logger.error("Finding-capture sandbox probe failed: %s", reason)
                return 78
            with app.app_context():
                outcome = process_one_capture_job()
            logger.info("Finding capture worker finished with outcome=%s", outcome)
        elif not args.loop:
            logger.info("Finding capture worker idle; no queue job claimed")
            return 0

        if not args.loop:
            return 0
        shutdown_requested.wait(poll_seconds)

    logger.info("Finding capture worker stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
