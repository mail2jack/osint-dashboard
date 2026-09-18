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
PROBE_TIMEOUT_MS = 15_000
DEFAULT_UNIT = Path(__file__).resolve().parent.parent / "deploy" / "osint-finding-capture-worker.service"


def verify_sandbox(*, unit_path: Path, chromium_path: Path | None) -> tuple[bool, list[str]]:
    """Return whether static worker-sandbox prerequisites are met.

    The dedicated worker may not rely on ``--no-sandbox``.  Either the Chromium
    systemd must allow the browser to create user namespaces.  ``NoNewPrivileges``
    deliberately remains enabled, so a setuid sandbox helper is not accepted as
    an alternative.  The executable is still probed separately before a job is
    claimed.
    """
    reasons: list[str] = []
    try:
        unit_text = unit_path.read_text(encoding="utf-8")
    except OSError:
        return False, ["worker unit is unreadable"]

    # Unit comments document the forbidden flags, so inspect only actual
    # systemd directives.  A comment must never turn a safe unit into NO_GO.
    directives = "\n".join(
        line for line in unit_text.splitlines() if not line.lstrip().startswith("#")
    )
    if "--no-sandbox" in directives or "--disable-setuid-sandbox" in directives:
        reasons.append("worker unit permits an unsafe Chromium sandbox bypass")
    if "User=osint" not in unit_text:
        reasons.append("worker unit must run as osint")
    if "NoNewPrivileges=true" not in unit_text:
        reasons.append("worker unit must enforce NoNewPrivileges")

    namespaces_allowed = "RestrictNamespaces=false" in unit_text
    if chromium_path is None:
        reasons.append("Chromium executable path is not configured")
    elif not chromium_path.is_file() or not os.access(chromium_path, os.X_OK):
        reasons.append("Chromium executable is missing or not executable")
    if not namespaces_allowed:
        reasons.append(
            "no verified Chromium sandbox: worker must allow Chromium user namespaces"
        )
    return not reasons, reasons


def probe_sandbox(chromium_path: Path) -> tuple[bool, str]:
    """Launch and close Chromium on ``about:blank`` to prove its sandbox works.

    This is not a capture: it has no target URL, credentials, queue or evidence
    write. It uses Playwright just like the worker does, so browser shutdown is
    explicit instead of relying on Chromium's unreliable command-line dump
    mode. It never uses a sandbox-bypass flag.
    """
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return False, "Chromium sandbox probe could not run"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(chromium_path),
                headless=True,
                timeout=PROBE_TIMEOUT_MS,
                ignore_default_args=["--no-sandbox"],
                args=[
                    "--no-first-run",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--disable-extensions",
                    "--disable-crash-reporter",
                ],
            )
            try:
                context = browser.new_context()
                try:
                    page = context.new_page()
                    if page.url != "about:blank":
                        return False, "Chromium sandbox probe did not open about:blank"
                finally:
                    context.close()
            finally:
                browser.close()
    except (OSError, Error):
        return False, "Chromium sandbox probe could not run"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", type=Path, default=DEFAULT_UNIT)
    parser.add_argument("--chromium", type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    ok, reasons = verify_sandbox(unit_path=args.unit, chromium_path=args.chromium)
    if ok and args.probe:
        ok, reason = probe_sandbox(args.chromium)
        reasons = [] if ok else [reason]
    print(json.dumps({"classification": "GO" if ok else "NO_GO", "reasons": reasons}))
    return 0 if ok else NO_GO_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
