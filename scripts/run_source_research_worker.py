#!/usr/bin/env python3
"""Dedicated worker entry point for passive workflow source research.

This process is inert by default.  It starts or refreshes at most one durable
SpiderFoot-backed workflow request per polling cycle, outside Gunicorn.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="poll until stopped")
    args = parser.parse_args(argv)

    if os.environ.get("WORKFLOW_SOURCE_RESEARCH_WORKER_ENABLED") != "1":
        logger.info("Workflow source-research worker disabled; no scan claimed")
        return 0

    poll_seconds = int(os.environ.get("WORKFLOW_SOURCE_RESEARCH_POLL_SECONDS", "15"))
    if poll_seconds < 5:
        logger.error("Source-research worker poll interval must be at least 5 seconds")
        return 78

    from app import app
    from cms.services.workflow_source_research_worker import process_one_source_research

    while True:
        with app.app_context():
            outcome = process_one_source_research()
        logger.info("Workflow source-research worker outcome=%s", outcome)
        if not args.loop:
            return 0
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
