"""Canonical photo-analysis file storage.

Central helper used by both the photo-analysis upload route and the async
worker so the storage root is defined in exactly one place.

Canonical root: ``<instance_path>/photo_analysis``. In production this is
``/opt/osint-dashboard/instance/photo_analysis`` (Flask ``instance_path``),
which:

- is listed in the systemd ``ReadWritePaths`` for the dashboard service, so
  ``os.makedirs``/``save`` succeed without read-only filesystem errors;
- is *not* served by Flask's ``/static`` (the static folder is
  ``<root>/static``), so uploaded research photos are never publicly
  reachable and require no new public serve route.

Design decisions
----------------
- File names are server-generated UUIDs; the extension is derived from
  validated magic bytes (``cms.image_validation.validate_image_file``), never
  from the client-supplied Content-Type header or original filename.
- ``data_value`` stores only the file *name* (relative to the canonical
  root). Absolute local paths never reach the UI, audit logs, worker-facing
  values, or error responses.
- ``resolve_photo_path`` only ever returns paths that live under the
  canonical root (via a ``commonpath`` guard). Historical `photo_analysis`
  rows whose ``data_value`` is an absolute path under the old
  ``/cms/static/uploads/photos`` location will not resolve, so the worker
  produces a clean "photo unavailable" finding instead of a 500.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta

from flask import current_app

from cms.image_validation import validate_image_file

logger = logging.getLogger(__name__)

PHOTO_ANALYSIS_DIRNAME = "photo_analysis"

# Redelijke maximale uploadgrootte per foto (consistent met screenshot-limit).
MAX_PHOTO_FILE_BYTES = 8 * 1024 * 1024  # 8 MB

# Magic-byte detected formats we actually accept for photo analysis.
# jpeg/jpg normalized to "jpg".
DETECTED_TO_EXT = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "gif": "gif", "webp": "webp"}

# Stale-file retention. Analyses run async; retries within this window reuse
# the uploaded file, so we never delete anything younger than this.
STALE_FILE_MAX_AGE = timedelta(hours=6)

# Sweep budget per call so cleanup never walks unbounded directories.
_SWEEP_LIMIT = 50


def photo_analysis_dir() -> str:
    """Absolute path of the canonical photo-analysis storage root."""
    return os.path.join(current_app.instance_path, PHOTO_ANALYSIS_DIRNAME)


def ensure_photo_analysis_dir() -> str:
    """Create (and return) the canonical photo-analysis storage root."""
    directory = photo_analysis_dir()
    os.makedirs(directory, exist_ok=True)
    return directory


def allowed_detected_format(detected: str) -> str | None:
    """Map a magic-byte detected format to a canonical extension, or None."""
    if not detected:
        return None
    return DETECTED_TO_EXT.get(detected.lower())


def validate_photo(file_storage):
    """Validate an uploaded photo by magic bytes.

    Returns ``(is_valid, ext)`` where ``ext`` is the canonical extension
    (``jpg``/``png``/``gif``/``webp``) or ``""``. The file cursor is left at
    the beginning after validation (``validate_image_file`` rewind behavior).
    """
    if file_storage is None:
        return False, ""
    is_valid, detected = validate_image_file(file_storage)
    if not is_valid:
        return False, ""
    ext = allowed_detected_format(detected)
    if not ext:
        return False, ""
    return True, ext


def store_photo(file_storage, ext: str) -> str:
    """Persist a validated photo under the canonical root.

    Returns the server-generated file *name* (relative to the root). Raises on
    write failure; a partially written file is removed before re-raising so no
    orphan stays behind.
    """
    directory = ensure_photo_analysis_dir()
    filename = f"{uuid.uuid4().hex[:12]}.{ext}"
    filepath = os.path.join(directory, filename)
    try:
        file_storage.save(filepath)
    except OSError:
        unlink_photo(filepath)
        raise
    return filename


def _path_under_root(path: str) -> bool:
    """Whether a resolved path is inside the canonical root (no traversal)."""
    root = os.path.realpath(photo_analysis_dir())
    resolved = os.path.realpath(path)
    try:
        common = os.path.commonpath([root, resolved])
    except ValueError:
        return False
    return common == root


def resolve_photo_path(data_value: str | None) -> str | None:
    """Resolve a stored name/path to an absolute path under the canonical root.

    Accepts plain names (current format) and historical absolute paths, but
    only if they resolve inside the canonical root. Returns ``None`` for old
    ``/cms/static`` paths, other roots, removable files, or non-existent files
    so the worker can emit a clean "photo unavailable" finding.
    """
    if not data_value:
        return None
    root_real = os.path.realpath(photo_analysis_dir())
    if os.path.isabs(data_value):
        candidate = data_value
    else:
        candidate = os.path.join(root_real, data_value)
    if not _path_under_root(candidate):
        return None
    if not os.path.isfile(candidate):
        return None
    return os.path.realpath(candidate)


def unlink_photo(data_value: str | None) -> None:
    """Best-effort removal of a single file under the canonical root.

    Never removes anything outside the canonical root (traversal-safe). Any
    missing file or permission error is logged and swallowed, so a cleanup
    failure never masks the original analysis/route error.
    """
    if not data_value:
        return
    candidate = data_value
    if not os.path.isabs(candidate):
        candidate = os.path.join(os.path.realpath(photo_analysis_dir()), candidate)
    if not _path_under_root(candidate):
        return
    try:
        os.unlink(candidate)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove photo-analysis file %s: %s", candidate, exc)


def sweep_stale_photos() -> int:
    """Remove photo-analysis files older than the retention window.

    Bounded (max ``_SWEEP_LIMIT`` deletions per call) and only touches
    regular files directly inside the canonical root. Returns the number of
    removed files.
    """
    root = os.path.realpath(photo_analysis_dir())
    if not os.path.isdir(root):
        return 0
    cutoff = datetime.now() - STALE_FILE_MAX_AGE
    removed = 0
    try:
        entries = os.listdir(root)
    except OSError:
        return 0
    for entry in entries:
        if removed >= _SWEEP_LIMIT:
            break
        path = os.path.join(root, entry)
        if not os.path.isfile(path):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            continue
        if mtime < cutoff:
            unlink_photo(path)
            removed += 1
    if removed:
        logger.info("Swept %d stale photo-analysis file(s) from %s", removed, root)
    return removed