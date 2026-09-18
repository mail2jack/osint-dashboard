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

    # A subsequent PR must provide an executor.  Refuse before importing the
    # app or claiming a job so an accidental environment toggle can never
    # create a half-processed capture.
    logger.error("No sandboxed finding-capture executor is installed")
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
