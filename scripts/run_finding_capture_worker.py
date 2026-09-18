#!/usr/bin/env python3
"""Dedicated FEAT-1 capture-worker entry point.

This process is deliberately inert until a future sandboxed executor is
reviewed and installed.  It is separate from Gunicorn so web requests can
never run a browser.  The initial unit may safely be installed but is neither
enabled nor capable of falling back to an unsafe browser configuration.
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

from verify_finding_capture_sandbox import verify_sandbox


def main() -> int:
    if os.environ.get("FINDING_CAPTURE_WORKER_ENABLED") != "1":
        logger.info("Finding capture worker disabled; no queue job claimed")
        return 0

    verified, reasons = verify_sandbox(
        unit_path=Path(__file__).resolve().parent.parent
        / "deploy"
        / "osint-finding-capture-worker.service",
        chromium_path=(
            Path(os.environ["FINDING_CAPTURE_CHROMIUM_PATH"])
            if os.environ.get("FINDING_CAPTURE_CHROMIUM_PATH")
            else None
        ),
    )
    if not verified:
        logger.error("Finding-capture sandbox verification failed: %s", "; ".join(reasons))
        return 78

    # Import the app only after the sandbox has passed.  This keeps the worker
    # separate from Gunicorn and ensures an accidental enablement can never
    # claim a queue job on an unverified runtime.
    from app import app
    from cms.services.finding_capture_worker import process_one_capture_job

    with app.app_context():
        outcome = process_one_capture_job()
    logger.info("Finding capture worker finished with outcome=%s", outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
