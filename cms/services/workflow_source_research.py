"""Queue contract for workflow-native, passive SpiderFoot research.

The workflow web request creates a durable ``ResearchAction`` plus a linked
``SpiderFootScan`` record.  It never calls SpiderFoot itself: a dedicated
worker claims the pending scan later.  That boundary keeps long-running source
research out of Gunicorn and makes the action, its audit trail and its
investigation scope durable across web-worker restarts.

Only SpiderFoot's passive use case is exposed here.  The legacy SpiderFoot UI
may retain broader controls for super-admin operations, but ordinary workflow
users do not receive raw module or active-scan controls.
"""

from __future__ import annotations

import json
import uuid

from cms.models import InvestigationStatus, SpiderFootScan, db
from cms.spiderfoot_service import SpiderFootService
from cms.workflow.models import WorkflowResearchAction


class SourceResearchRejected(ValueError):
    """The request cannot safely become a workflow source-research action."""


# Stable API keys (left) map to SpiderFoot's documented target type names.
# Every listed type is available to the native workflow, but the worker always
# starts it with ``use_case='passive'``.
TARGET_TYPES = {
    "person": SpiderFootService.TARGET_TYPES["person"],
    "username": SpiderFootService.TARGET_TYPES["username"],
    "email": SpiderFootService.TARGET_TYPES["email"],
    "phone_number": SpiderFootService.TARGET_TYPES["phone_number"],
    "domain": SpiderFootService.TARGET_TYPES["domain"],
    "subdomain": SpiderFootService.TARGET_TYPES["subdomain"],
    "url": SpiderFootService.TARGET_TYPES["url"],
    "ip": SpiderFootService.TARGET_TYPES["ip"],
    "ipv6": SpiderFootService.TARGET_TYPES["ipv6"],
    "company": SpiderFootService.TARGET_TYPES["company"],
    "organization": SpiderFootService.TARGET_TYPES["organization"],
    "vessel": SpiderFootService.TARGET_TYPES["vessel"],
    "license_plate": SpiderFootService.TARGET_TYPES["license_plate"],
    "vin": SpiderFootService.TARGET_TYPES["vin"],
}

_IDENTITY_TARGETS = frozenset({"person", "username", "email", "phone_number"})
_ORGANISATION_TARGETS = frozenset({"company", "organization", "domain", "subdomain", "url"})
_NETWORK_TARGETS = frozenset({"ip", "ipv6"})


def passive_profile_for(target_type: str) -> str:
    """Return the fixed curated profile for an allowed target type.

    The profile is an implementation choice, not a caller-controlled module
    list.  SpiderFoot receives ``use_case='passive'`` separately, so no native
    workflow endpoint can opt into footprint, investigate or all modes.
    """
    if target_type in _IDENTITY_TARGETS:
        return "investigation"
    if target_type in _ORGANISATION_TARGETS:
        return "company"
    if target_type in _NETWORK_TARGETS:
        return "threat_hunt"
    return "basic"


def validate_target(target_type: object, target_value: object) -> tuple[str, str]:
    """Validate bounded, non-control-character target input before any write."""
    if not isinstance(target_type, str) or target_type not in TARGET_TYPES:
        raise SourceResearchRejected("Unsupported source-research target type")
    if not isinstance(target_value, str):
        raise SourceResearchRejected("Target must be a string")
    target = target_value.strip()
    if not target:
        raise SourceResearchRejected("Target is required")
    if len(target) > 500:
        raise SourceResearchRejected("Target must be at most 500 characters")
    if any(ord(char) < 32 for char in target):
        raise SourceResearchRejected("Target contains control characters")
    return target_type, target


def queue_passive_source_research(
    *, case, investigation, actor, target_type: object, target_value: object, subject=None
) -> tuple[WorkflowResearchAction, SpiderFootScan]:
    """Add one native passive-research action and its pending scan atomically.

    The caller owns the final audit entry and transaction.  Nothing external
    starts until that transaction commits and the separate worker claims the
    scan.  All parent relations are checked explicitly; FORCE RLS remains the
    independent database backstop.
    """
    target_type, target = validate_target(target_type, target_value)

    if not case or not investigation or not actor:
        raise SourceResearchRejected("Case, investigation and actor are required")
    if case.tenant_id != investigation.tenant_id or case.id != investigation.case_id:
        raise SourceResearchRejected("Investigation does not belong to this case")
    if actor.tenant_id != case.tenant_id:
        raise SourceResearchRejected("Actor does not belong to this tenant")
    if (
        investigation.status != InvestigationStatus.OPEN.value
        or investigation.archived_at is not None
    ):
        raise SourceResearchRejected("Investigation is not open")
    if subject is not None:
        if subject.tenant_id != case.tenant_id:
            raise SourceResearchRejected("Subject does not belong to this tenant")
        if not case.subjects.filter_by(id=subject.id).first():
            raise SourceResearchRejected("Subject is not linked to this case")

    action = WorkflowResearchAction(
        id=str(uuid.uuid4()),
        tenant_id=case.tenant_id,
        case_id=case.id,
        subject_id=subject.id if subject is not None else None,
        investigation_id=investigation.id,
        target_kind="subject" if subject is not None else "investigation",
        target_snapshot=json.dumps(
            {
                "subject_id": subject.id if subject is not None else None,
                "target_type": target_type,
                "target_value": target,
            }
        ),
        action_type="source_research",
        data_value=target,
        label="Verdiept brononderzoek",
        status="pending",
        result_summary="Queued for passive source research",
        created_by=actor.id,
    )
    db.session.add(action)
    db.session.flush()

    # ``scan_id`` is required by the legacy table.  This deterministic local
    # placeholder is replaced with SpiderFoot's real id by the worker after it
    # starts the scan; it is never sent to SpiderFoot.
    scan = SpiderFootScan(
        tenant_id=case.tenant_id,
        scan_id=f"queued:{action.id}",
        scan_name=f"Passive source research: {target}"[:300],
        target_value=target,
        target_type=TARGET_TYPES[target_type],
        case_id=case.id,
        subject_id=subject.id if subject is not None else None,
        investigation_id=investigation.id,
        research_action_id=action.id,
        use_case="passive",
        profile=passive_profile_for(target_type),
        module_ids=[],
        status="pending",
        created_by=actor.id,
    )
    db.session.add(scan)
    db.session.flush()
    return action, scan
