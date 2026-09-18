"""Sandbox-gated browser capture primitive for FEAT-1 evidence.

This module is deliberately not imported by Flask routes.  A future dedicated
worker may call it only after :mod:`scripts.verify_finding_capture_sandbox`
returns GO.  It never uses Chromium's unsafe sandbox-bypass switches and
revalidates every browser request through the strict capture SSRF guard.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from cms.services.ssrf_guard import install_capture_request_guard, validate_capture_url

CAPTURE_TIMEOUT_MS = 30_000
MAX_CAPTURE_PNG_BYTES = 8 * 1024 * 1024
CAPTURE_VIEWPORT = {"width": 1280, "height": 720}


def _configured_chromium_path() -> str | None:
    """Return an explicitly configured, executable browser path if present."""
    configured = os.environ.get("FINDING_CAPTURE_CHROMIUM_PATH")
    if not configured:
        return None
    chromium = Path(configured)
    if not chromium.is_file() or not os.access(chromium, os.X_OK):
        raise CaptureExecutionError("Configured Chromium executable is unavailable")
    return str(chromium)


class CaptureExecutionError(RuntimeError):
    """A capture cannot safely become evidence."""


@dataclass(frozen=True)
class CapturedPage:
    """In-memory evidence returned to the dedicated worker for persistence."""

    png_bytes: bytes
    source_url: str
    title: str


def capture_page_as_png(target_url: str) -> CapturedPage:
    """Capture a public page into bounded PNG bytes without unsafe fallbacks.

    The caller is responsible for the independent runtime-sandbox preflight.
    This function does no database work and writes no files, so a worker can
    persist the screenshot, provenance and queue transition atomically later.
    """
    valid, reason = validate_capture_url(target_url)
    if not valid:
        raise CaptureExecutionError(f"Capture target rejected: {reason}")

    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as exc:  # pragma: no cover - deployment prerequisite
        raise CaptureExecutionError("Playwright is not installed") from exc

    try:
        with sync_playwright() as playwright:
            # Deliberately no Chromium sandbox-bypass flags: especially never
            # --no-sandbox or --disable-setuid-sandbox. The optional executable
            # path is a root-owned, AppArmor-approved browser installed by the
            # dedicated operator script. A failed sandbox remains a hard error.
            # Playwright adds ``--no-sandbox`` to Chromium's default launch
            # arguments.  That is not acceptable for evidence capture: remove
            # that default explicitly rather than merely avoiding it in our
            # own ``args`` list.
            launch_options = {
                "headless": True,
                "timeout": CAPTURE_TIMEOUT_MS,
                "ignore_default_args": ["--no-sandbox"],
                "args": ["--disable-crash-reporter"],
            }
            chromium_path = _configured_chromium_path()
            if chromium_path is not None:
                launch_options["executable_path"] = chromium_path
            browser = playwright.chromium.launch(**launch_options)
            try:
                context = browser.new_context(viewport=CAPTURE_VIEWPORT)
                try:
                    page = context.new_page()
                    install_capture_request_guard(page)
                    response = page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=CAPTURE_TIMEOUT_MS,
                    )
                    if response is None:
                        raise CaptureExecutionError("Capture navigation failed")
                    valid, reason = validate_capture_url(page.url)
                    if not valid:
                        raise CaptureExecutionError(
                            f"Capture redirect rejected: {reason}"
                        )
                    png_bytes = page.screenshot(
                        type="png", full_page=False, timeout=CAPTURE_TIMEOUT_MS
                    )
                    if not isinstance(png_bytes, bytes) or not png_bytes:
                        raise CaptureExecutionError("Capture produced no PNG data")
                    if len(png_bytes) > MAX_CAPTURE_PNG_BYTES:
                        raise CaptureExecutionError("Capture PNG exceeds size limit")
                    return CapturedPage(
                        png_bytes=png_bytes,
                        source_url=page.url,
                        title=(page.title() or "")[:300],
                    )
                finally:
                    context.close()
            finally:
                browser.close()
    except CaptureExecutionError:
        raise
    except (Error, TimeoutError) as exc:
        raise CaptureExecutionError("Browser capture failed") from exc
