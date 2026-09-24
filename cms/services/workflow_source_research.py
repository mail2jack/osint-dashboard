"""Queue contract for workflow-native deep source research.

The workflow web request creates a durable ``ResearchAction`` plus a linked
``SpiderFootScan`` record.  It never calls SpiderFoot itself: a dedicated
worker claims the pending scan later.  That boundary keeps long-running source
research out of Gunicorn and makes the action, its audit trail and its
investigation scope durable across web-worker restarts.

The native workflow exposes curated intensity levels, never raw third-party
module identifiers.  This keeps the user-facing contract stable while storing
the exact resolved module set for an auditable, reproducible worker run.
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

# These values are workflow policy, rather than a reflection of every raw
# SpiderFoot switch.  ``all`` is deliberately a curated maximum, not a pass-
# through to arbitrary modules.  Profiles below are resolved to an immutable
# list at queue time so a later profile edit cannot alter a queued scan.
SCAN_INTENSITIES = ("passive", "footprint", "investigate", "all")
_INTENSITY_PROFILES = {
    "passive": {"identity": "investigation", "organisation": "company", "network": "threat_hunt", "other": "basic"},
    "footprint": {"identity": "full", "organisation": "company", "network": "threat_hunt", "other": "full"},
    "investigate": {"identity": "full", "organisation": "full", "network": "threat_hunt", "other": "full"},
    "all": {"identity": "full", "organisation": "full", "network": "threat_hunt", "other": "full"},
}


def _target_family(target_type: str) -> str:
    if target_type in _IDENTITY_TARGETS:
        return "identity"
    if target_type in _ORGANISATION_TARGETS:
        return "organisation"
    if target_type in _NETWORK_TARGETS:
        return "network"
    return "other"


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


def resolve_scan_configuration(
    target_type: str, intensity: object = "passive", expert_module_ids: object = None
) -> tuple[str, list[str]]:
    """Resolve a bounded, reproducible profile and allowlisted module set.

    The route separately authorizes advanced intensities and expert mode.  The
    service still validates every value, so malformed requests cannot reach the
    worker as an uncontrolled module selection.
    """
    if not isinstance(intensity, str) or intensity not in SCAN_INTENSITIES:
        raise SourceResearchRejected("Unsupported source-research intensity")
    profile = _INTENSITY_PROFILES[intensity][_target_family(target_type)]
    allowed = list(SpiderFootService.INVESTIGATION_PROFILES[profile]["modules"])
    if expert_module_ids is None:
        return profile, allowed
    if not isinstance(expert_module_ids, list) or not expert_module_ids:
        raise SourceResearchRejected("Expert modules must be a non-empty list")
    if len(expert_module_ids) > len(allowed) or any(
        not isinstance(module, str) or module not in allowed for module in expert_module_ids
    ):
        raise SourceResearchRejected("Expert modules are not allowed for this target")
    # Deduplicate while preserving the deliberate expert selection order.
    modules = list(dict.fromkeys(expert_module_ids))
    if not modules:
        raise SourceResearchRejected("Expert modules are required")
    return profile, modules


def expert_module_options() -> list[str]:
    """Return the bounded module allowlist for the super-admin UI only."""
    return sorted(
        {
            module
            for profile in _INTENSITY_PROFILES["all"].values()
            for module in SpiderFootService.INVESTIGATION_PROFILES[profile]["modules"]
        }
    )


def expert_module_options_by_target() -> dict[str, dict[str, list[str]]]:
    """Return the exact curated choices for each expert UI combination."""
    return {
        target_type: {
            intensity: resolve_scan_configuration(target_type, intensity)[1]
            for intensity in SCAN_INTENSITIES
        }
        for target_type in TARGET_TYPES
    }


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


def spiderfoot_seed_for(target_type: str, target: str) -> tuple[str, str]:
    """Return SpiderFoot's unambiguous seed syntax for a native target.

    SpiderFoot infers its seed type from the target string; its HTTP API does
    not accept a separate target-type field.  Human names and usernames are
    therefore quoted deliberately.  Without this conversion a plain name
    (for example ``Lindsey Jonker``) is rejected by SpiderFoot before a scan
    can start.
    """
    if target_type in {"person", "username"}:
        # Quotes are syntax in SpiderFoot's seed language, never user data.
        # Reject them rather than allowing an input value to change that
        # syntax or to be interpreted as a different target kind.
        if '"' in target:
            raise SourceResearchRejected("Target must not contain quotation marks")
        return f'"{target}"', "HUMAN_NAME" if target_type == "person" else "USERNAME"
    return target, TARGET_TYPES[target_type]


def queue_passive_source_research(
    *,
    case,
    investigation=None,
    actor,
    target_type: object,
    target_value: object,
    subject=None,
    intensity: object = "passive",
    expert_module_ids: object = None,
) -> tuple[WorkflowResearchAction, SpiderFootScan]:
    """Add one native source-research action and its pending scan atomically.

    The caller owns the final audit entry and transaction.  Nothing external
    starts until that transaction commits and the separate worker claims the
    scan.  All parent relations are checked explicitly; FORCE RLS remains the
    independent database backstop.
    """
    target_type, target = validate_target(target_type, target_value)
    spiderfoot_target, spiderfoot_target_type = spiderfoot_seed_for(target_type, target)
    profile, module_ids = resolve_scan_configuration(
        target_type, intensity, expert_module_ids
    )

    if not case or not actor:
        raise SourceResearchRejected("Case and actor are required")
    if investigation is not None and (
        case.tenant_id != investigation.tenant_id or case.id != investigation.case_id
    ):
        raise SourceResearchRejected("Investigation does not belong to this case")
    if actor.tenant_id != case.tenant_id:
        raise SourceResearchRejected("Actor does not belong to this tenant")
    if investigation is not None and (
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
        investigation_id=investigation.id if investigation is not None else None,
        target_kind="subject" if subject is not None else "case",
        target_snapshot=json.dumps(
            {
                "subject_id": subject.id if subject is not None else None,
                "target_type": target_type,
                "target_value": target,
                "scan_intensity": intensity,
                "expert_mode": expert_module_ids is not None,
                "profile": profile,
            }
        ),
        action_type="source_research",
        data_value=target,
        label="Verdiept brononderzoek",
        status="pending",
        result_summary="Queued for deep source research",
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
        scan_name=f"Deep source research: {target}"[:300],
        target_value=spiderfoot_target,
        target_type=spiderfoot_target_type,
        case_id=case.id,
        subject_id=subject.id if subject is not None else None,
        investigation_id=investigation.id if investigation is not None else None,
        research_action_id=action.id,
        use_case=intensity,
        profile=profile,
        module_ids=module_ids,
        status="pending",
        created_by=actor.id,
    )
    db.session.add(scan)
    db.session.flush()
    return action, scan
