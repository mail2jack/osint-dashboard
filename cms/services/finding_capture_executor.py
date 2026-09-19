"""Sandbox-gated browser capture primitive for FEAT-1 evidence.

This module is deliberately not imported by Flask routes.  A future dedicated
worker may call it only after :mod:`scripts.verify_finding_capture_sandbox`
returns GO.  It never uses Chromium's unsafe sandbox-bypass switches and
revalidates every browser request through the strict capture SSRF guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path

from cms.services.ssrf_guard import install_capture_request_guard, validate_capture_url

CAPTURE_TIMEOUT_MS = 30_000
MAX_CAPTURE_PNG_BYTES = 8 * 1024 * 1024
CAPTURE_VIEWPORT = {"width": 1280, "height": 720}
# A navigation reaching ``domcontentloaded`` is not proof that a modern
# client-rendered page has usable content.  Keep this short and bounded: it is
# a quality gate for evidence, not an attempt to wait indefinitely for every
# analytics request on the page.
CAPTURE_RENDER_TIMEOUT_MS = 6_000
CAPTURE_POST_RENDER_SETTLE_MS = 3_000
MAX_LOADING_INDICATORS_FOR_SPARSE_PAGE = 2
MIN_RENDERED_TEXT_LENGTH_FOR_LOADING_PAGE = 160
MIN_RENDERED_TEXT_LENGTH = 80


def _validate_capture_png_quality(png_bytes: bytes) -> None:
    """Reject a technically valid but visually empty PNG.

    DOM readiness alone is insufficient for some social-media interstitials:
    they can expose accessible text and media nodes while Chromium paints a
    wholly blank viewport.  Sampling a small decoded image makes that failure
    deterministic without attempting content recognition or storing anything.
    """
    try:
        from PIL import Image

        with Image.open(BytesIO(png_bytes)) as image:
            # Resizing bounds both memory and CPU use.  A one-colour thumbnail
            # means the rendered viewport contains no visual evidence at all.
            sample = image.convert("RGB").resize((64, 36))
            if len(set(sample.get_flattened_data())) < 2:
                raise CaptureExecutionError(
                    "Capture screenshot contained no usable visual content"
                )
    except CaptureExecutionError:
        raise
    except Exception as exc:
        raise CaptureExecutionError("Capture produced an invalid PNG") from exc


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


def _wait_for_rendered_content(page, timeout_error_type: type[BaseException]) -> None:
    """Wait briefly for client rendering and reject a persistent skeleton.

    The browser is intentionally allowed to continue when a page never becomes
    network-idle: many legitimate sites maintain long-lived connections.  A
    page that still has several loading/placeholder nodes *and* virtually no
    readable text after the bounded wait is different: saving that as evidence
    would be misleading, so it fails closed instead.
    """
    loading_selector = (
        '[aria-busy="true"], [class*="skeleton" i], '
        '[class*="placeholder" i]'
    )
    try:
        page.wait_for_function(
            "selector => document.querySelectorAll(selector).length === 0",
            loading_selector,
            timeout=CAPTURE_RENDER_TIMEOUT_MS,
        )
    except timeout_error_type:
        # Inspect the final page state below.  A busy websocket must not by
        # itself make an otherwise rendered page unusable.
        pass

    page.wait_for_timeout(CAPTURE_POST_RENDER_SETTLE_MS)
    readiness = page.evaluate(
        """selector => {
            const body = document.body;
            const text = (body?.innerText || '').replace(/\\s+/g, ' ').trim();
            const visibleMedia = [...document.querySelectorAll('img, video, canvas')]
                .filter(node => {
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width * rect.height >= 10_000;
                }).length;
            return {
                loading_indicators: document.querySelectorAll(selector).length,
                text_length: text.length,
                visible_media: visibleMedia,
            };
        }""",
        loading_selector,
    )
    if not isinstance(readiness, dict):
        raise CaptureExecutionError("Could not verify rendered capture content")
    loading_indicators = readiness.get("loading_indicators")
    text_length = readiness.get("text_length")
    visible_media = readiness.get("visible_media")
    if (
        not isinstance(loading_indicators, int)
        or not isinstance(text_length, int)
        or not isinstance(visible_media, int)
    ):
        raise CaptureExecutionError("Could not verify rendered capture content")
    if (
        loading_indicators > MAX_LOADING_INDICATORS_FOR_SPARSE_PAGE
        and text_length < MIN_RENDERED_TEXT_LENGTH_FOR_LOADING_PAGE
    ):
        raise CaptureExecutionError("Capture page remained in a loading state")
    if text_length < MIN_RENDERED_TEXT_LENGTH and visible_media == 0:
        raise CaptureExecutionError("Capture page did not render usable content")


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
                    _wait_for_rendered_content(page, TimeoutError)
                    # The first viewport may legitimately contain a hero,
                    # consent wall, or lazy-loading placeholder while useful
                    # public evidence is rendered below it.  Capture the page
                    # rather than only the 1280×720 viewport; the existing
                    # byte limit remains the fail-closed bound.
                    png_bytes = page.screenshot(
                        type="png", full_page=True, timeout=CAPTURE_TIMEOUT_MS
                    )
                    if not isinstance(png_bytes, bytes) or not png_bytes:
                        raise CaptureExecutionError("Capture produced no PNG data")
                    if len(png_bytes) > MAX_CAPTURE_PNG_BYTES:
                        raise CaptureExecutionError("Capture PNG exceeds size limit")
                    _validate_capture_png_quality(png_bytes)
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
