#!/usr/bin/env python3
"""Post-canary check: confirm the FINAL close report was produced and log a summary.

Runs once shortly after osint-canary-close timer (fires +3 min after close) to
verify the FINAL=.../STATUS=... evidence landed in reports/rollout/ and emit a
journal summary for the operator.  Local report + journal only (no outbound).

Mirrors osint-health-refresh: absolute ExecStart keeps repo root importable.
"""

from __future__ import annotations

import fcntl
import glob
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[canary_check] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROLLOUT_DIR = Path("/opt/osint-dashboard/reports/rollout")

LOCK_PATH = "/tmp/osint-canary-check.lock"


def _journal(msg: str) -> None:
    subprocess.run(["logger", "-t", "osint-canary-check", "--", msg])


def main() -> int:
    with open(LOCK_PATH, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning("canary-check already running; flock contention")
            return 0

        reports = sorted(glob.glob(str(ROLLOUT_DIR / "canary-close-gunicorn-*.txt")))
        if not reports:
            logger.warning("no canary-close report found under %s", ROLLOUT_DIR)
            _journal("osint-canary-check NO_REPORT: canary-close produced no report file")
            return 1

        latest = Path(reports[-1])
        text = latest.read_text()
        status_line = next((ln for ln in text.splitlines() if ln.startswith("STATUS")), "STATUS : ?")
        final_line = next((ln for ln in text.splitlines() if ln.startswith("FINAL ")), "FINAL : ?")
        stamp = latest.name
        msgs = []
        if "FINAL : True" in text:
            msgs.append(f"osint-canary-check OK report={stamp} {final_line.strip()} {status_line.strip()}")
        else:
            msgs.append(f"osint-canary-check EARLY/PENDING report={stamp} {final_line.strip()} {status_line.strip()}")

        for m in msgs:
            _journal(m)
        logger.info("%s -> %s | %s", stamp, final_line.strip(), status_line.strip())
        return 0


if __name__ == "__main__":
    sys.exit(main())