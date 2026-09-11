"""Central service layer for investigation lifecycle mutations (PR2).

The canonical case-scoped routes and the legacy id-only wrappers converge on
this module so archive/restore/update share one validation, one status check
and one audit trail. Operations mutate the passed investigation and add an
``AuditLog`` entry in the **same** database session; the route owns the
``commit`` (or ``rollback`` when the transaction fails so the audit cannot be
written without the mutation).

Validators (:func:`require_open`, :func:`require_archived`) are separate from
access control (``ensure_investigation_access``/``ensure_case_access``) and
raise :class:`OperationalConflict` for 409-style rejections. Callers decide how
to render the error (JSON body or flash + redirect).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from flask import request
from flask_babel import gettext as _

from cms.models import AuditLog, Investigation, InvestigationStatus

logger = logging.getLogger(__name__)

TITLE_MAX_LENGTH = 300
INSTRUCTIONS_MAX_LENGTH = 5000
NOTES_MAX_LENGTH = 5000

ALLOWED_UPDATE_FIELDS = ("title", "instructions", "notes")

# Messages reused by routes (flash/JSON) — matches the legacy wrappers.
ARCHIVED_MESSAGE = "Investigation is already archived"
NOT_ARCHIVED_MESSAGE = "Investigation is not archived"
ARCHIVED_UNEDITABLE_MESSAGE = "Investigation is archived and cannot be edited."


class OperationalConflict(Exception):
    """A 409-class rejection raised by the operational status validators."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def require_open(investigation: Investigation) -> None:
    """Operational validator: only *open* investigations may be mutated.

    Positive invariant — ``status is OPEN`` **and** ``archived_at is None``.
    Unknown or inconsistent status/timestamp combinations (e.g. a ``closed``
    status, or an open status with an archive timestamp) are rejected with 409.
    Independent of access control; raises :class:`OperationalConflict`.
    """
    if (
        investigation.status != InvestigationStatus.OPEN.value
        or investigation.archived_at is not None
    ):
        raise OperationalConflict(ARCHIVED_UNEDITABLE_MESSAGE)


def require_archived(investigation: Investigation) -> None:
    """Operational validator: restore only applies to *archived* investigations.

    Positive invariant — ``status is ARCHIVED`` **and** ``archived_at`` is set;
    everything else is rejected with 409.
    """
    if (
        investigation.status != InvestigationStatus.ARCHIVED.value
        or investigation.archived_at is None
    ):
        raise OperationalConflict(NOT_ARCHIVED_MESSAGE)


def validate_update_payload(payload: object) -> dict:
    """Normalize and validate an update payload.

    The payload must be a JSON object whose provided fields are strings or
    ``null`` — anything else (arrays, numbers, booleans, nested values) is
    rejected with 400 before any mutation or audit. Only
    ``title``/``instructions``/``notes`` are accepted; any other key is
    rejected. Returns a kwargs dict with exactly the provided fields, ready for
    :func:`update_investigation`. Provided-but-empty ``instructions``/``notes``
    are normalized to ``None`` (clearing the field).
    """
    if not isinstance(payload, dict):
        raise ValueError(_("Payload must be a JSON object."))

    unknown = [key for key in payload if key not in ALLOWED_UPDATE_FIELDS]
    if unknown:
        raise ValueError(
            _("Unknown fields: %(fields)s", fields=", ".join(sorted(unknown)))
        )

    for key in ALLOWED_UPDATE_FIELDS:
        if key in payload and payload[key] is not None and not isinstance(payload[key], str):
            raise ValueError(
                _("%(field)s must be a string.", field=key.capitalize())
            )

    normalized: dict = {}
    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            raise ValueError(_("Title is required."))
        if len(title) > TITLE_MAX_LENGTH:
            raise ValueError(
                _("Title must be at most %(max)d characters.", max=TITLE_MAX_LENGTH)
            )
        normalized["title"] = title
    if "instructions" in payload:
        value = (payload.get("instructions") or "").strip() or None
        if value is not None and len(value) > INSTRUCTIONS_MAX_LENGTH:
            raise ValueError(
                _(
                    "Instructions must be at most %(max)d characters.",
                    max=INSTRUCTIONS_MAX_LENGTH,
                )
            )
        normalized["instructions"] = value
    if "notes" in payload:
        value = (payload.get("notes") or "").strip() or None
        if value is not None and len(value) > NOTES_MAX_LENGTH:
            raise ValueError(
                _("Notes must be at most %(max)d characters.", max=NOTES_MAX_LENGTH)
            )
        normalized["notes"] = value
    if not normalized:
        raise ValueError(_("No updates provided."))
    return normalized


def _request_meta() -> tuple:
    """Return ``(ip_address, user_agent)`` when inside a request context."""
    try:
        return request.remote_addr, request.user_agent.string
    except RuntimeError:
        return None, None


_UNSET = object()


def update_investigation(
    investigation: Investigation,
    *,
    actor_id,
    title=_UNSET,
    instructions=_UNSET,
    notes=_UNSET,
) -> Investigation:
    """Apply an update to an *open* investigation and audit it atomically.

    ``..._UNSET`` values leave the corresponding field untouched, so partial
    updates work. When nothing actually changes the call is an idempotent
    no-op and no ``AuditLog`` entry is written. Raised validators are not
    caught here: the caller decides between 409 and a redirect.
    """
    require_open(investigation)

    old_values = {
        "title": investigation.title,
        "instructions": investigation.instructions,
        "notes": investigation.notes,
    }
    new_values = dict(old_values)
    if title is not _UNSET:
        new_values["title"] = title
    if instructions is not _UNSET:
        new_values["instructions"] = instructions
    if notes is not _UNSET:
        new_values["notes"] = notes

    if new_values == old_values:
        return investigation

    investigation.title = new_values["title"]
    investigation.instructions = new_values["instructions"]
    investigation.notes = new_values["notes"]

    ip_address, user_agent = _request_meta()
    AuditLog.log(
        user_id=actor_id,
        action="update",
        entity_type="investigation",
        entity_id=investigation.id,
        ip_address=ip_address,
        user_agent=user_agent,
        case_id=investigation.case_id,
        old_values=old_values,
        new_values=new_values,
        description=f"Workflow updated investigation: {investigation.human_number}",
    )

    logger.info(
        "investigation updated: user=%s investigation=%s fields=%s",
        actor_id,
        investigation.id,
        ",".join(k for k in ALLOWED_UPDATE_FIELDS if new_values[k] != old_values[k]),
    )
    return investigation


def archive_investigation(
    investigation: Investigation,
    *,
    actor_id,
) -> Investigation:
    """Archive an *open* investigation. Never mutates number, case_id, tenant_id."""
    require_open(investigation)

    old = {
        "status": investigation.status,
        "archived_at": investigation.archived_at.isoformat()
        if investigation.archived_at
        else None,
    }
    investigation.archived_at = datetime.now(UTC)
    investigation.status = InvestigationStatus.ARCHIVED.value
    new = {
        "status": investigation.status,
        "archived_at": investigation.archived_at.isoformat()
        if investigation.archived_at
        else None,
    }

    ip_address, user_agent = _request_meta()
    AuditLog.log(
        user_id=actor_id,
        action="archive",
        entity_type="investigation",
        entity_id=investigation.id,
        ip_address=ip_address,
        user_agent=user_agent,
        case_id=investigation.case_id,
        old_values=old,
        new_values=new,
        description=f"Workflow archived investigation: {investigation.human_number}",
    )
    return investigation


def restore_investigation(
    investigation: Investigation,
    *,
    actor_id,
) -> Investigation:
    """Restore an *archived* investigation back to open."""
    require_archived(investigation)

    old = {
        "status": investigation.status,
        "archived_at": investigation.archived_at.isoformat()
        if investigation.archived_at
        else None,
    }
    investigation.archived_at = None
    investigation.status = InvestigationStatus.OPEN.value
    new = {"status": investigation.status, "archived_at": investigation.archived_at}

    ip_address, user_agent = _request_meta()
    AuditLog.log(
        user_id=actor_id,
        action="restore",
        entity_type="investigation",
        entity_id=investigation.id,
        ip_address=ip_address,
        user_agent=user_agent,
        case_id=investigation.case_id,
        old_values=old,
        new_values=new,
        description=f"Workflow restored investigation: {investigation.human_number}",
    )
    return investigation