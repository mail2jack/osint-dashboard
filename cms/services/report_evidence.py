"""Safe representation of finding-screenshot evidence in reports.

Reports are a presentation boundary: an untrusted URL stored on a screenshot
row must never become an image request from a reader's browser or from the PDF
renderer.  A thumbnail/original is therefore exposed only when its on-disk
file is confined to the private finding-screenshot store.  The captured page
URL remains a normal, escaped http(s) reference.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from urllib.parse import urlparse

from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename


MAX_REPORT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REPORT_IMAGE_PIXELS = 20_000_000
PDF_THUMBNAIL_SIZE = (480, 320)


def safe_source_url(value: str | None) -> str | None:
    """Return whether *value* is a safe-to-link external reference."""
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and bool(parsed.netloc) else None


def _screenshot_root() -> Path:
    return (Path(current_app.instance_path) / "finding_screenshots").resolve()


def _local_evidence_file(screenshot) -> tuple[Path, str] | None:
    """Resolve only regular files beneath the private screenshot root.

    The database value is historical input, so both traversal and legacy paths
    outside the private store are deliberately ignored rather than rendered.
    """
    if not isinstance(screenshot.file_path, str) or not screenshot.file_path:
        return None
    candidate = Path(screenshot.file_path)
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(_screenshot_root())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or len(relative.parts) != 2:
        return None
    finding_id, filename = relative.parts
    if finding_id != str(screenshot.finding_id) or secure_filename(filename) != filename:
        return None
    if resolved.stat().st_size > MAX_REPORT_IMAGE_BYTES:
        return None
    return resolved, filename


def _pdf_thumbnail_data_uri(path: Path) -> str | None:
    """Create a bounded JPEG thumbnail for the server-side PDF only."""
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > MAX_REPORT_IMAGE_PIXELS:
                return None
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail(PDF_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def report_screenshot(screenshot, *, for_pdf: bool = False) -> dict:
    """Return a safe, template-ready representation of one screenshot row."""
    result = {
        "thumbnail_url": None,
        "original_url": None,
        "pdf_thumbnail_data_uri": None,
        # Some historic rows only populated ``url``.  Preserve a safe external
        # reference as provenance, but never use it as an image resource.
        "source_url": safe_source_url(screenshot.source_url)
        or safe_source_url(screenshot.url),
        "notes": screenshot.notes,
        "captured_at": screenshot.captured_at,
        "file_size": screenshot.file_size,
        "capture_provenance": screenshot.capture_provenance,
    }
    local = _local_evidence_file(screenshot)
    if not local:
        return result
    path, filename = local
    original_url = url_for(
        "workflow.serve_screenshot",
        finding_id=screenshot.finding_id,
        filename=filename,
    )
    result["thumbnail_url"] = original_url
    result["original_url"] = original_url
    if for_pdf:
        result["pdf_thumbnail_data_uri"] = _pdf_thumbnail_data_uri(path)
    return result


def report_screenshots(finding, *, for_pdf: bool = False) -> list[dict]:
    """Build safe evidence representations for a finding's screenshots."""
    return [
        report_screenshot(screenshot, for_pdf=for_pdf)
        for screenshot in (finding.finding_screenshots or [])
    ]
