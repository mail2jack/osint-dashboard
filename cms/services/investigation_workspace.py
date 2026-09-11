"""Read-model for the Investigation Workspace detail page (PR3).

Pure read path for ``/case/<case_id>/investigations/<investigation_id>``: the
research actions scoped to an investigation, the subjects those actions
reference, the findings derived from those actions (via the ``action_findings``
junction) and a read-only activity timeline built from ``AuditLog``.

Everything is isolated to one investigation and one tenant:

- every query filters on ``tenant_id`` and ``case_id`` explicitly (RLS is a
  backstop, never the only protection);
- only *this* investigation's actions count as "in scope" — a finding that also
  links to a case-wide action keeps only scoped action ids in ``FindingDTO``;
- the template only ever receives immutable DTOs, never ORM objects.

Operational ordering inside :func:`build_inv_workspace`:

1. run the ORM queries (actions, junction, findings + screenshots, audit +
   eager users, investigation creator, subjects ORM);
2. build the audit DTOs and the ``user_id -> name`` map (no decryption
   involved);
3. LAST: decrypt subjects and serialize subject DTOs, inside one
   ``no_autoflush`` block. No lazy loads or extra queries happen afterwards.

The timeline is bounded at ``CAP`` events and reports ``total``/``total_exact``
honestly (see :class:`TimelineResult`): scope-change (link/unlink) audits of
already-unlinked actions are discovered from a bounded window instead of an
unbounded scan, so the count can be an undercount when that window is full.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import sqlalchemy as sa

from cms.models import AuditLog, Investigation, Subject, User, db
from cms.workflow.actions.registry import ACTION_REGISTRY
from cms.workflow.models import (
    WorkflowActionFinding,
    WorkflowFinding,
    WorkflowResearchAction,
)

logger = logging.getLogger(__name__)

# Maximum number of timeline events rendered on the workspace page.
CAP = 50

# Sort key used for events without a timestamp (older than everything).
_EPOCH_FLOOR = -float("inf")

# Only http(s) may be rendered as a clickable link.
_LINKABLE_SCHEMES = ("http", "https")

# Audit ``action`` values known to write scope changes (ADR-0005 D3/D4).
_SCOPE_AUDIT_ACTIONS = ("link", "unlink")


# ---------------------------------------------------------------------------
# DTOs (immutable read models — the template only sees these)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ActionDTO:
    id: str
    action_type: str
    label: str
    icon: str | None
    status: str
    data_value: str | None
    subject_id: str | None
    target_kind: str | None
    created_at: datetime | None
    completed_at: datetime | None
    archived: bool
    finding_count: int  # unieke findings gekoppeld aan deze action


@dataclasses.dataclass(frozen=True, slots=True)
class ScreenshotDTO:
    url: str | None
    source_url: str | None
    captured_at: datetime | None
    notes: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class SubjectDTO:
    id: str
    name: str | None
    subject_type: str | None
    display_name: str
    decrypted: dict  # plain-text fields only; empty when decrypt not needed


@dataclasses.dataclass(frozen=True, slots=True)
class FindingDTO:
    id: str
    title: str
    detail: str | None
    source_url: str | None
    source_url_is_linkable: bool
    source_type: str | None
    status: str | None
    verified: bool
    created_at: datetime | None
    screenshots: list[ScreenshotDTO]
    # Only the actions of THIS investigation that produced this finding.
    # A finding that also belongs to a case-wide action does not list it here.
    action_ids: list[str]
    action_labels: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class TimelineEventDTO:
    id: str
    timestamp: datetime | None
    action: str  # raw audit action code; the template translates it
    entity_type: str
    entity_id: str | None
    entity_display: str  # action label / finding title (or fallback)
    description: str | None
    user_name: str | None
    diff: dict | None  # {"status": ("open", "archived")} etc., or None


@dataclasses.dataclass(frozen=True, slots=True)
class TimelineResult:
    events: list[TimelineEventDTO]
    total: int
    total_exact: bool
    truncated: bool


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceDTO:
    actions: list[ActionDTO]
    subjects: list[SubjectDTO]
    findings: list[FindingDTO]
    finding_actions_map: dict[str, list[str]]  # scoped finding_id -> scoped action_ids
    timeline: TimelineResult
    created_by_name: str | None  # investigation creator, from the same user map
    counts: dict  # {"actions": N, "subjects": N, "findings": N}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _action_display_label(action: WorkflowResearchAction) -> str:
    """Human label for an action (registry label when available)."""
    return (
        action.label
        or ACTION_REGISTRY.get(action.action_type, {}).get("label")
        or action.action_type
    )


def _action_icon(action: WorkflowResearchAction) -> str | None:
    return ACTION_REGISTRY.get(action.action_type, {}).get("icon")


def _is_linkable_url(raw: str | None) -> bool:
    """Only http(s) URLs may be rendered as a clickable ``href``."""
    if not raw:
        return False
    try:
        return urlparse(raw).scheme.lower() in _LINKABLE_SCHEMES
    except ValueError:
        return False


def _sort_timestamp(dt: datetime | None) -> float:
    """Consistent UTC sort key — never mix naive/aware comparisons."""
    if dt is None:
        return _EPOCH_FLOOR
    if dt.tzinfo is not None:
        return dt.timestamp()
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _compute_diff(old: object, new: object) -> dict | None:
    """Diff between two ``SafeJSON``-decoded value objects (None when empty)."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    changes: dict[str, tuple[object, object]] = {}
    for key in sorted(set(old) | set(new)):
        old_val, new_val = old.get(key), new.get(key)
        if old_val != new_val:
            changes[key] = (old_val, new_val)
    return changes or None


def _screenshot_dtos(finding: WorkflowFinding) -> list[ScreenshotDTO]:
    return [
        ScreenshotDTO(
            url=ss.url,
            source_url=ss.source_url,
            captured_at=ss.captured_at,
            notes=ss.notes,
        )
        for ss in (finding.finding_screenshots or [])
    ]


# ---------------------------------------------------------------------------
# Query loaders (public — used by build_inv_workspace and by isolation tests)
# ---------------------------------------------------------------------------


def load_inv_actions(*, tenant_id: str, case_id: str, investigation_id: str) -> list[ActionDTO]:
    """Scoped research actions of one investigation (``created_at ASC, id ASC``)."""
    actions = (
        WorkflowResearchAction.query.filter(
            WorkflowResearchAction.tenant_id == tenant_id,
            WorkflowResearchAction.case_id == case_id,
            WorkflowResearchAction.investigation_id == investigation_id,
        )
        .order_by(
            WorkflowResearchAction.created_at.asc(),
            WorkflowResearchAction.id.asc(),
        )
        .all()
    )
    return [
        ActionDTO(
            id=a.id,
            action_type=a.action_type,
            label=_action_display_label(a),
            icon=_action_icon(a),
            status=a.status,
            data_value=a.data_value,
            subject_id=a.subject_id,
            target_kind=a.target_kind,
            created_at=a.created_at,
            completed_at=a.completed_at,
            archived=a.archived_at is not None,
            finding_count=0,  # filled by build_inv_workspace
        )
        for a in actions
    ]


def load_inv_subjects(*, tenant_id: str, subject_ids: Iterable[str]) -> list[SubjectDTO]:
    """Subjects behind the given ids, soft-deleted excluded, decrypted DTOs.

    The caller is responsible for passing only subject ids that the current
    investigation's actions resolved to; this loader independently enforces the
    tenant filter and the soft-delete filter.
    """
    ids = sorted(set(subject_ids))
    if not ids:
        return []
    subjects = (
        Subject.query.filter(
            Subject.tenant_id == tenant_id,
            Subject.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            Subject.id.in_(ids),
        )
        .all()
    )
    return _subject_dtos(subjects)


def load_inv_findings(
    *, tenant_id: str, case_id: str, action_ids: Iterable[str]
) -> tuple[list[FindingDTO], dict[str, list[str]]]:
    """Findings derived from the given actions, deduped and filtered.

    Returns ``(findings, finding_actions_map)``. ``finding_actions_map`` maps
    each surviving finding id to the scoped action ids that produced it — the
    junction is resolved only against the supplied (in-scope) action ids, so a
    case-wide action never shows up here.
    """
    action_ids = sorted(set(action_ids))
    if not action_ids:
        return [], {}
    junction = (
        WorkflowActionFinding.query.filter(
            WorkflowActionFinding.action_id.in_(action_ids)
        ).all()
    )
    finding_ids = sorted({link.finding_id for link in junction})
    if not finding_ids:
        return [], {}
    findings = (
        WorkflowFinding.query.filter(
            WorkflowFinding.tenant_id == tenant_id,
            WorkflowFinding.case_id == case_id,
            WorkflowFinding.is_deleted == False,  # noqa: E712
            WorkflowFinding.archived_at.is_(None),
            WorkflowFinding.id.in_(finding_ids),
        )
        .options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
        .order_by(
            WorkflowFinding.created_at.desc(),
            WorkflowFinding.id.desc(),
        )
        .all()
    )
    survivors = {f.id for f in findings}
    scoped_actions = WorkflowResearchAction.query.filter(
        WorkflowResearchAction.id.in_(action_ids)
    ).all()
    label_icon = {a.id: _action_display_label(a) for a in scoped_actions}

    finding_actions_map: dict[str, list[str]] = {}
    for link in junction:
        if link.finding_id not in survivors:
            continue
        finding_actions_map.setdefault(link.finding_id, []).append(link.action_id)

    dtos = [
        FindingDTO(
            id=f.id,
            title=f.title,
            detail=f.detail,
            source_url=f.source_url,
            source_url_is_linkable=_is_linkable_url(f.source_url),
            source_type=f.source_type,
            status=f.status,
            verified=f.verified,
            created_at=f.created_at,
            screenshots=_screenshot_dtos(f),
            action_ids=sorted(finding_actions_map.get(f.id, [])),
            action_labels=[
                label_icon[a_id]
                for a_id in sorted(finding_actions_map.get(f.id, []))
            ],
        )
        for f in findings
    ]
    return dtos, finding_actions_map


def load_inv_timeline(
    *,
    tenant_id: str,
    case_id: str,
    investigation_id: str,
    action_ids: Iterable[str],
    finding_ids: Iterable[str],
    cap: int = CAP,
) -> TimelineResult:
    """Read-only activity timeline for one investigation (bounded at ``cap``).

    Channel A: audits of the currently scoped actions and findings.
    Channel B (supplementary only): link/unlink audits from a bounded window of
    the case's most recent ``cap`` link/unlink audits, restricted to events
    whose ``entity_id`` is NOT in the current action ids (so a currently linked
    action's own link audit is counted exactly once, via channel A).
    """
    action_ids = sorted(set(action_ids))
    finding_ids = sorted(set(finding_ids))
    current_action_ids = set(action_ids)

    # ---- Channel A: everything belonging to the current scope ----
    if not action_ids and not finding_ids:
        scope_a_events: list[AuditLog] = []
        total_a = 0
    else:
        filter_a = (
            AuditLog.tenant_id == tenant_id,
            AuditLog.case_id == case_id,
            sa.or_(
                sa.and_(
                    AuditLog.entity_type == "research_action",
                    AuditLog.entity_id.in_(action_ids),
                ),
                sa.and_(
                    AuditLog.entity_type == "finding",
                    AuditLog.entity_id.in_(finding_ids),
                ),
            ),
        )
        scope_a_events = (
            AuditLog.query.options(sa.orm.joinedload(AuditLog.user))
            .filter(*filter_a)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .limit(cap)
            .all()
        )
        total_a = AuditLog.query.filter(*filter_a).count()

    # ---- Channel B: link/unlink scope changes (supplementary only) ----
    filter_b = (
        AuditLog.tenant_id == tenant_id,
        AuditLog.case_id == case_id,
        AuditLog.entity_type == "research_action",
        AuditLog.action.in_(_SCOPE_AUDIT_ACTIONS),
    )
    link_unlink_audits = (
        AuditLog.query.options(sa.orm.joinedload(AuditLog.user))
        .filter(*filter_b)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(cap)
        .all()
    )
    total_b_case = AuditLog.query.filter(*filter_b).count()
    b_window_full = len(link_unlink_audits) >= cap and total_b_case > cap

    supplementary_b = [
        e
        for e in link_unlink_audits
        if e.entity_id not in current_action_ids
        and (
            (e.old_values or {}).get("investigation_id") == investigation_id
            or (e.new_values or {}).get("investigation_id") == investigation_id
        )
    ]

    # ---- Merge, dedup on audit id, total before the cap ----
    all_events: dict[str, AuditLog] = {}
    for e in scope_a_events:
        all_events[e.id] = e
    for e in supplementary_b:
        all_events.setdefault(e.id, e)

    # Channel A count is exact; supplementary B never overlaps channel A by
    # construction (entity_id not in the current action ids). Defensive spread
    # in case an audit id ever shows up on both sides.
    a_ids = {e.id for e in scope_a_events}
    b_ids = {e.id for e in supplementary_b}
    overlap = sorted(a_ids & b_ids)
    total = total_a + len(supplementary_b) - len(overlap)

    ordered = sorted(
        all_events.values(),
        key=lambda e: (_sort_timestamp(e.timestamp), e.id),
        reverse=True,
    )[:cap]

    return TimelineResult(
        events=[_timeline_event_dto(e) for e in ordered],
        total=total,
        total_exact=not b_window_full,
        truncated=total > len(ordered) or b_window_full,
    )


# ---------------------------------------------------------------------------
# DTO building helpers
# ---------------------------------------------------------------------------


def _timeline_event_dto(event: AuditLog) -> TimelineEventDTO:
    return TimelineEventDTO(
        id=event.id,
        timestamp=event.timestamp,
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        entity_display="",  # filled by build_inv_workspace via the display maps
        description=event.description,
        user_name=event.user_name,
        diff=_compute_diff(event.old_values, event.new_values),
    )


def _subject_dtos(subjects: list[Subject]) -> list[SubjectDTO]:
    """Decrypt and serialize subjects inside one no_autoflush block.

    Decryption is in-place on ORM objects; do NOT flush afterwards. Only plain
    columns are copied into the DTO — no lazy-loaded relationships are touched
    after decryption.
    """
    with db.session.no_autoflush:
        for s in subjects:
            s.decrypt_identifiers()
        dtos = []
        for s in subjects:
            dtos.append(
                SubjectDTO(
                    id=s.id,
                    name=s.name,
                    subject_type=s.subject_type,
                    display_name=(
                        s.compute_name()
                        if callable(getattr(s, "compute_name", None))
                        else (s.name or "")
                    ),
                    decrypted=_subject_plain_fields(s),
                )
            )
        dtos.sort(key=lambda d: (d.display_name.lower(), d.id))
        return dtos


def _subject_plain_fields(subject: Subject) -> dict:
    """Copy the (already decrypted) plain columns the template renders.

    Deliberately explicit — never a blanket ``to_dict(decrypted=True)`` (that
    would lazy-load relationships such as ``contacts``/``addresses``).
    """
    return {
        "risk_score": subject.risk_score,
        "geslacht": subject.geslacht,
        "date_of_birth": subject.date_of_birth,
        "place_of_birth": subject.place_of_birth,
        "nationality": subject.nationality,
        "bsn_number": subject.bsn_number,
        "bank_account": subject.bank_account,
        "street": subject.street,
        "house_number": subject.house_number,
        "house_number_addition": subject.house_number_addition,
        "postal_code": subject.postal_code,
        "city": subject.city,
        "email": subject.email,
        "phone": subject.phone,
        "notes": subject.notes,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_inv_workspace(investigation: Investigation, case) -> WorkspaceDTO:
    """Build the full read-model for one investigation (PR3 detail page)."""
    tenant_id = investigation.tenant_id
    case_id = investigation.case_id
    investigation_id = investigation.id

    # 1. All ORM queries up front.
    actions_orm = (
        WorkflowResearchAction.query.filter(
            WorkflowResearchAction.tenant_id == tenant_id,
            WorkflowResearchAction.case_id == case_id,
            WorkflowResearchAction.investigation_id == investigation_id,
        )
        .order_by(
            WorkflowResearchAction.created_at.asc(),
            WorkflowResearchAction.id.asc(),
        )
        .all()
    )
    action_ids = [a.id for a in actions_orm]

    junction = (
        WorkflowActionFinding.query.filter(
            WorkflowActionFinding.action_id.in_(action_ids)
        ).all()
        if action_ids
        else []
    )
    finding_ids = sorted({link.finding_id for link in junction})

    findings_orm: list[WorkflowFinding] = []
    if finding_ids:
        findings_orm = (
            WorkflowFinding.query.filter(
                WorkflowFinding.tenant_id == tenant_id,
                WorkflowFinding.case_id == case_id,
                WorkflowFinding.is_deleted == False,  # noqa: E712
                WorkflowFinding.archived_at.is_(None),
                WorkflowFinding.id.in_(finding_ids),
            )
            .options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
            .order_by(
                WorkflowFinding.created_at.desc(),
                WorkflowFinding.id.desc(),
            )
            .all()
        )
    survivors = {f.id for f in findings_orm}

    timeline = load_inv_timeline(
        tenant_id=tenant_id,
        case_id=case_id,
        investigation_id=investigation_id,
        action_ids=action_ids,
        finding_ids=finding_ids,
    )

    # Investigation creator name shares the timeline user map (no extra query).
    creator_name = ""
    if investigation.created_by:
        creator = db.session.get(User, investigation.created_by)
        if creator:
            creator_name = creator.username or creator.full_name or ""

    subject_ids = sorted({a.subject_id for a in actions_orm if a.subject_id})
    subjects_orm: list[Subject] = []
    if subject_ids:
        subjects_orm = (
            Subject.query.filter(
                Subject.tenant_id == tenant_id,
                Subject.is_deleted == False,  # noqa: E712
                Subject.id.in_(subject_ids),
            )
            .all()
        )

    # 2. Action/finding/timeline DTOs + display maps (no decryption here).
    action_display = {a.id: _action_display_label(a) for a in actions_orm}
    action_icon_map = {a.id: _action_icon(a) for a in actions_orm}
    finding_display = {f.id: f.title for f in findings_orm}

    # Channel-B events can reference actions that are no longer in scope
    # (unlinked); resolve their labels in one extra query so the timeline never
    # falls back to a raw entity id.
    extra_action_ids = sorted(
        {
            e.entity_id
            for e in timeline.events
            if e.entity_type == "research_action"
            and e.entity_id is not None
            and e.entity_id not in action_display
        }
    )
    if extra_action_ids:
        extra_actions = WorkflowResearchAction.query.filter(
            WorkflowResearchAction.id.in_(extra_action_ids)
        ).all()
        action_display.update(
            {a.id: _action_display_label(a) for a in extra_actions}
        )

    finding_actions_map: dict[str, list[str]] = {}
    for link in junction:
        if link.finding_id not in survivors:
            continue
        finding_actions_map.setdefault(link.finding_id, []).append(link.action_id)
    finding_action_labels: dict[str, list[str]] = {}
    for finding_id, f_action_ids in finding_actions_map.items():
        finding_action_labels[finding_id] = [
            action_display[a_id] for a_id in sorted(f_action_ids) if a_id in action_display
        ]

    action_dtos = [
        ActionDTO(
            id=a.id,
            action_type=a.action_type,
            label=action_display[a.id],
            icon=action_icon_map.get(a.id),
            status=a.status,
            data_value=a.data_value,
            subject_id=a.subject_id,
            target_kind=a.target_kind,
            created_at=a.created_at,
            completed_at=a.completed_at,
            archived=a.archived_at is not None,
            finding_count=sum(1 for fs in finding_actions_map.values() if a.id in fs),
        )
        for a in actions_orm
    ]
    finding_dtos = [
        FindingDTO(
            id=f.id,
            title=f.title,
            detail=f.detail,
            source_url=f.source_url,
            source_url_is_linkable=_is_linkable_url(f.source_url),
            source_type=f.source_type,
            status=f.status,
            verified=f.verified,
            created_at=f.created_at,
            screenshots=_screenshot_dtos(f),
            action_ids=sorted(finding_actions_map.get(f.id, [])),
            action_labels=finding_action_labels.get(f.id, []),
        )
        for f in findings_orm
    ]

    timeline_events = [
        TimelineEventDTO(
            id=e.id,
            timestamp=e.timestamp,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            entity_display=(
                action_display.get(e.entity_id)
                or finding_display.get(e.entity_id)
                or (e.entity_id or "")
            ),
            description=e.description,
            user_name=e.user_name,
            diff=e.diff,
        )
        for e in timeline.events
    ]
    timeline = TimelineResult(
        events=timeline_events,
        total=timeline.total,
        total_exact=timeline.total_exact,
        truncated=timeline.truncated,
    )

    # 3. LAST: subject decryption + serialization (no queries afterwards).
    subject_dtos = _subject_dtos(subjects_orm) if subjects_orm else []

    return WorkspaceDTO(
        actions=action_dtos,
        subjects=subject_dtos,
        findings=finding_dtos,
        finding_actions_map=finding_actions_map,
        timeline=timeline,
        created_by_name=creator_name or None,
        counts={
            "actions": len(action_dtos),
            "subjects": len(subject_dtos),
            "findings": len(finding_dtos),
        },
    )