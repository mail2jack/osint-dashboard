#!/usr/bin/env python3
"""Read-only preflight for the dedicated FEAT-1 browser sandbox.

This deliberately does *not* start Chromium or access the network.  It only
answers whether the worker unit and a Chromium installation meet the minimum
conditions to attempt a separately-reviewed capture executor.  A NO_GO result
is the safe and expected result until an operator installs a supported sandbox.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


NO_GO_EXIT = 78
DEFAULT_UNIT = Path(__file__).resolve().parent.parent / "deploy" / "osint-finding-capture-worker.service"


def verify_sandbox(
    *, unit_path: Path, chromium_path: Path | None
) -> tuple[bool, list[str]]:
    """Return whether static worker-sandbox prerequisites are met.

    The dedicated worker may not rely on ``--no-sandbox``.  Either the Chromium
    setuid helper must be present with its expected ownership/mode, or systemd
    must allow the browser to create user namespaces.  The current foundation
    intentionally meets neither condition and therefore remains NO_GO.
    """
    reasons: list[str] = []
    try:
        unit_text = unit_path.read_text(encoding="utf-8")
    except OSError:
        return False, ["worker unit is unreadable"]

    if "--no-sandbox" in unit_text or "--disable-setuid-sandbox" in unit_text:
        reasons.append("worker unit permits an unsafe Chromium sandbox bypass")
    if "User=osint" not in unit_text:
        reasons.append("worker unit must run as osint")
    if "NoNewPrivileges=true" not in unit_text:
        reasons.append("worker unit must enforce NoNewPrivileges")

    namespaces_restricted = "RestrictNamespaces=true" in unit_text
    helper_ok = False
    if chromium_path is None:
        reasons.append("Chromium executable path is not configured")
    elif not chromium_path.is_file() or not os.access(chromium_path, os.X_OK):
        reasons.append("Chromium executable is missing or not executable")
    else:
        helper = chromium_path.parent / "chrome-sandbox"
        try:
            helper_stat = helper.stat()
        except OSError:
            helper_stat = None
        if helper_stat is not None:
            helper_ok = (
                helper_stat.st_uid == 0
                and helper_stat.st_gid == 0
                and (helper_stat.st_mode & 0o7777) == 0o4755
            )

    if namespaces_restricted and not helper_ok:
        reasons.append(
            "no verified Chromium sandbox: namespaces are restricted and chrome-sandbox is unavailable"
        )
    if not namespaces_restricted and not helper_ok:
        reasons.append(
            "user-namespace sandbox requires an operator-verified runtime probe"
        )
    return not reasons, reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", type=Path, default=DEFAULT_UNIT)
    parser.add_argument("--chromium", type=Path)
    args = parser.parse_args(argv)
    ok, reasons = verify_sandbox(unit_path=args.unit, chromium_path=args.chromium)
    print(json.dumps({"classification": "GO" if ok else "NO_GO", "reasons": reasons}))
    return 0 if ok else NO_GO_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
