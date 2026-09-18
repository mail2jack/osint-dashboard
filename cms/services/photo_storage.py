"""Canonical photo-analysis file storage (tenant-scoped).

Central helper used by both the photo-analysis upload route and the async
worker so the storage root is defined in exactly one place.

Storage layout::

    <instance_path>/photo_analysis/<tenant_id>/<uuid4.hex>.<ext>

- ``<instance_path>`` on production is ``/opt/osint-dashboard/instance``,
  which is listed in the systemd ``ReadWritePaths`` and is *not* served by
  Flask's ``/static`` folder — uploaded research photos are never publicly
  reachable and no public serve route is added.
- ``<tenant_id>`` isolates tenants on disk. Every helper call (store / resolve
  / unlink / sweep) takes the tenant it operates on; a data_value from tenant
  A can never resolve or delete a file of tenant B, even if the stored
  ``data_value`` is hand-manipulated.
- File names are fully random ``uuid4().hex`` (32 hex chars); the extension
  is derived from validated magic bytes (``cms.image_validation``), never
  from the client-supplied Content-Type header or original filename.

data_value contract
-------------------
``action.data_value`` stores only the file *name* (relative to the tenant's
directory under the root). Absolute local paths never reach the UI, audit
logs, worker-facing values, or error responses. Historical rows that hold an
absolute path under the old ``/cms/static/uploads/photos`` location resolve to
``None`` (fail-safe "photo unavailable" finding in the worker) instead of a
500.

Lifecycle policy
----------------
Uploads are transient:
- The worker deletes the file after analysis (success or failure) via
  ``unlink_photo``.
- If a worker crashes mid-run, the row ends in ``pending``/``running`` and
  ``sweep_stale_photos`` never touches it (it is still referenced).
- Only files *not referenced by any pending/running* photo_analysis action
  and older than the retention window are removed by the sweep. The sweep is
  tenant-scoped, bounded per call, and never bypasses RLS.
"""

import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

from flask import current_app

from cms.image_validation import validate_image_file

logger = logging.getLogger(__name__)

PHOTO_ANALYSIS_DIRNAME = "photo_analysis"

# Max upload per single photo (mirrors the screenshot limit).
MAX_PHOTO_FILE_BYTES = 8 * 1024 * 1024  # 8 MB per file

# Hard per-tenant cap on transient photo-analysis storage (bytes + count).
# Photos are transient (deleted after analysis / by sweep) but pending uploads
# for one tenant must not be able to fill the disk.
PHOTO_ANALYSIS_TENANT_MAX_BYTES = 256 * 1024 * 1024  # 256 MB per tenant
PHOTO_ANALYSIS_TENANT_MAX_FILES = 256


# Magic-byte detected formats we actually accept for photo analysis.
DETECTED_TO_EXT = {
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "gif": "gif",
    "webp": "webp",
}

# Tenant component is a single path segment: no separators, no traversal,
# no dots, safe on every filesystem. Drop-in for tenant ids (uuid strings).
_TENANT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9-_]{1,64}$")

# Stale-file retention. Files referenced by a pending/running action are never
# removed; unreferenced leftovers older than this are swept.
STALE_FILE_MAX_AGE = timedelta(hours=6)

# Sweep budget per call so cleanup never walks unbounded directories.
_SWEEP_LIMIT = 50
_TENANT_QUOTA_LOCKS: dict[str, threading.Lock] = {}
_TENANT_QUOTA_LOCKS_GUARD = threading.Lock()


class InvalidTenantComponent(ValueError):
    """Raised when a tenant id is not usable as a storage directory component."""


def _validate_tenant_component(tenant_id) -> str:
    """Validate a tenant id as a single safe directory component."""
    if not tenant_id:
        raise InvalidTenantComponent("tenant_id is required")
    if not isinstance(tenant_id, str) or not _TENANT_COMPONENT_RE.match(tenant_id):
        raise InvalidTenantComponent("invalid tenant_id for storage path")
    return tenant_id


def photo_analysis_root() -> str:
    """Absolute path of the canonical photo-analysis base (instance dir)."""
    return os.path.join(current_app.instance_path, PHOTO_ANALYSIS_DIRNAME)


def photo_analysis_dir(tenant_id: str) -> str:
    """Absolute path of one tenant's photo-analysis directory.

    The tenant component is strictly validated; the returned path is always a
    direct child of the canonical root (no traversal).
    """
    tenant_dir = _validate_tenant_component(tenant_id)
    return os.path.join(photo_analysis_root(), tenant_dir)


def allowed_detected_format(detected: str | None) -> str | None:
    """Map a magic-byte detected format to a canonical extension, or None."""
    if not detected:
        return None
    return DETECTED_TO_EXT.get(detected.lower())


def validate_photo(file_storage):
    """Validate an uploaded photo by magic bytes.

    Returns ``(is_valid, ext)`` where ``ext`` is the canonical extension
    (``jpg``/``png``/``gif``/``webp``) or ``""``. The file cursor is left at
    the beginning after validation (``validate_image_file`` rewinds it).

    Note: HEIC is intentionally NOT accepted — magic-byte detection does not
    cover HEIC, so we fail closed rather than trust a Content-Type header.
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


def tenant_photo_analysis_usage(tenant_id: str) -> tuple[int, int]:
    """Return (file_count, total_bytes) currently used by a tenant.

    Used for an honest quota: transient photo-analysis files are counted so
    multiple pending uploads cannot jointly exceed the per-tenant cap.
    """
    directory = photo_analysis_dir(tenant_id)
    if not os.path.isdir(directory):
        return 0, 0
    count = 0
    total = 0
    try:
        for entry in os.listdir(directory):
            path = os.path.join(directory, entry)
            if os.path.isfile(path):
                try:
                    total += os.path.getsize(path)
                    count += 1
                except OSError:
                    continue
    except OSError:
        return count, total
    return count, total


@contextmanager
def tenant_photo_quota_lock(tenant_id: str):
    """Serialize one tenant's transient quota operation."""
    from cms import db
    from sqlalchemy import text

    if db.engine.dialect.name == "postgresql":
        db.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:tenant_id, 0))"
            ),
            {"tenant_id": tenant_id},
        )
        try:
            yield
        finally:
            # Advisory locks are transaction-scoped. Roll back an unfinished
            # quota transaction so early quota returns also release the lock.
            # ``db.session`` is Flask-SQLAlchemy's scoped-session proxy;
            # transaction state lives on the concrete Session instance.
            if db.session().in_transaction():
                db.session.rollback()
        return

    with _TENANT_QUOTA_LOCKS_GUARD:
        lock = _TENANT_QUOTA_LOCKS.setdefault(tenant_id, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def store_photo(file_storage, ext: str, tenant_id: str) -> str:
    """Persist a validated photo under the tenant's canonical directory.

    Returns the full server-generated random file *name* (relative to the
    tenant directory, never an absolute path). Raises on write failure; a
    partially written file is removed before re-raising so no orphan stays.
    """
    directory = photo_analysis_dir(tenant_id)
    os.makedirs(directory, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(directory, filename)
    try:
        file_storage.save(filepath)
    except Exception:
        _unlink_raw(filepath)
        raise
    return filename


def _path_under_dir(path: str, directory: str) -> bool:
    """Whether a resolved path is inside `directory` (no traversal)."""
    root_real = os.path.realpath(directory)
    resolved = os.path.realpath(path)
    try:
        common = os.path.commonpath([root_real, resolved])
    except ValueError:
        return False
    return common == root_real


def resolve_photo_path(data_value: str | None, expected_tenant_id: str) -> str | None:
    """Resolve a stored name to an absolute path in the tenant's directory.

    - ``data_value`` must be the relative file name (current format);
    - absolute paths (historical rows) are fail-safe ``None`` → the worker
      emits a clean "photo unavailable" finding instead of a 500;
    - only paths under ``photo_analysis/<expected_tenant_id>`` resolve; a
      tenant A action can never resolve a tenant B file.
    """
    if not data_value:
        return None
    if os.path.isabs(data_value):
        return None
    tenant_dir = photo_analysis_dir(expected_tenant_id)
    candidate = os.path.join(os.path.realpath(tenant_dir), data_value)
    if not _path_under_dir(candidate, tenant_dir):
        return None
    if not os.path.isfile(candidate):
        return None
    return os.path.realpath(candidate)


def _unlink_raw(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove photo-analysis file %s: %s", path, exc)


def unlink_photo(data_value: str | None, expected_tenant_id: str) -> bool:
    """Best-effort removal of a single file in the tenant's directory.

    Returns ``True`` when the file was actually removed (used by the sweep to
    count only successful removals). Never removes anything outside the
    tenant's canonical directory (traversal-safe). Absolute values are
    ignored (fail-safe; historical rows).
    """
    if not data_value or os.path.isabs(data_value):
        return False
    tenant_dir = photo_analysis_dir(expected_tenant_id)
    candidate = os.path.join(os.path.realpath(tenant_dir), data_value)
    if not _path_under_dir(candidate, tenant_dir):
        return False
    try:
        os.unlink(candidate)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def collect_active_photo_data_values(tenant_id: str | None = None) -> set[str]:
    """Collect ``data_value`` names referenced by pending/running photo actions.

    Runs in the caller's tenant context (never bypasses RLS): under FORCE RLS
    the query is already row-filtered to the current tenant, and `tenant_id`
    is an additional defensive filter. The sweep uses these names to never
    delete a file an active action may still need (delayed worker / retry).
    """
    from cms.models import ResearchAction

    query = ResearchAction.query.filter(
        ResearchAction.action_type == "photo_analysis",
        ResearchAction.status.in_(["pending", "running"]),
    )
    if tenant_id:
        query = query.filter(ResearchAction.tenant_id == tenant_id)
    names = set()
    try:
        for value in query.with_entities(ResearchAction.data_value).all():
            if value and value[0]:
                names.add(value[0])
    except Exception:
        logger.warning(
            "Could not collect active photo-analysis references", exc_info=True
        )
    return names


def sweep_stale_photos(tenant_id: str, referenced: set[str] | None = None) -> int:
    """Remove unreferenced photo-analysis files older than the retention.

    - Only files in the *tenant's* directory are candidates (tenant-scoped).
    - Files whose name is in ``referenced`` (pending/running actions) are
      never removed, regardless of age.
    - Only regular files older than ``STALE_FILE_MAX_AGE`` are removed.
    - Bounded per call (``_SWEEP_LIMIT``), and only successful unlinks count.

    Returns the number of actually removed files.
    """
    if referenced is None:
        referenced = set()
    directory = photo_analysis_dir(tenant_id)
    if not os.path.isdir(directory):
        return 0
    cutoff = datetime.now() - STALE_FILE_MAX_AGE
    removed = 0
    try:
        entries = os.listdir(directory)
    except OSError:
        return 0
    for entry in entries:
        if removed >= _SWEEP_LIMIT:
            break
        if entry in referenced:
            continue
        path = os.path.join(directory, entry)
        if not os.path.isfile(path):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            continue
        if mtime < cutoff and unlink_photo(entry, tenant_id):
            removed += 1
    if removed:
        logger.info(
            "Swept %d stale photo-analysis file(s) for tenant %s", removed, tenant_id
        )
    return removed
