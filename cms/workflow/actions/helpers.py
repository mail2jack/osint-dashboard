import os
import logging
import uuid
from datetime import datetime

from cms.models import db, Subject
from cms.workflow.models import (
    WorkflowResearchAction,
    WorkflowActionFinding,
)

logger = logging.getLogger(__name__)


def _get_api_key(key_name):
    """Read API key from env with fallback to the main app's Setting table."""
    env_map = {
        "brave_api_key": "BRAVE_API_KEY",
        "hibp_api_key": "HIBP_API_KEY",
        "overheid_api_key": "OVERHEID_API_KEY",
        "rapidapi_username_key": "RAPIDAPI_USERNAME_KEY",
    }
    env_val = os.environ.get(env_map.get(key_name, key_name.upper()))
    if env_val:
        return env_val
    try:
        from flask import current_app
        from cms.models import Setting

        with current_app.app_context():
            s = Setting.query.filter_by(key=key_name).first()
            if s:
                return s.value
    except Exception:
        logger.debug("Failed to read API key %s from settings", key_name, exc_info=True)
    return None


def _action_subject(action):
    """Resolve the explicit target subject for an action (ADR-0001 PR4).

    Returns the subject linked to ``action.subject_id`` when set (subject-
    scoped action), else ``None`` for an explicit case-wide action. Handlers
    must never guess a subject from ``action.case.subjects``.
    """
    subject_id = getattr(action, "subject_id", None)
    if not subject_id:
        return None
    return db.session.get(Subject, subject_id)


# Recommended action presets per subject type. Actions are grouped/ordered for
# the investigation picker; unknown or absent types fall back to all actions.
SUBJECT_TYPE_PRESETS = {
    "person": [
        "email",
        "phone",
        "address",
        "social",
        "osint",
        "google_dork",
        "facebook",
        "instagram",
        "linkedin",
        "tiktok",
        "twitter",
        "photo_analysis",
    ],
    "company": [
        "kvk",
        "financial",
        "subdomain",
        "osint",
        "google_dork",
        "address",
    ],
    "organization": [
        "financial",
        "subdomain",
        "osint",
        "google_dork",
        "address",
    ],
    "vehicle": ["rdw"],
    "vessel": ["vessel", "osint", "google_dork"],
    "online": ["social", "osint", "google_dork"],
}


def presets_for_subject(subject_type):
    """Return the ordered preset action keys for a subject type."""
    return SUBJECT_TYPE_PRESETS.get(subject_type or "", [])


def _site_dork_search(platform_domain, query, subject_id=None, icon="🔍"):
    """Search for a person on a specific platform using site: dorks.

    Phase 1: Brave Search API (if key available)
    Phase 2: DDG fallback (if Brave returned nothing)

    Returns list of finding dicts with social_account when username is extractable.
    """
    from cms.social_extractor import (
        detect_platform,
        extract_username,
        MAJOR_SOCIAL_PLATFORMS,
    )
    from cms.services.search_service import brave_search, ddg_single_query

    findings = []
    dork = f'"{query}" site:{platform_domain}'

    # Phase 1: Brave Search
    brave_key = _get_api_key("brave_api_key")
    results = []
    if brave_key:
        try:
            results = brave_search(dork, api_key=brave_key)
        except Exception as e:
            logger.debug("Brave dork search failed for %s: %s", platform_domain, e)

    # Phase 2: DDG fallback
    if not results:
        try:
            ddg_raw = ddg_single_query(dork, max_results=8)
            results = [
                {
                    "url": r["url"],
                    "title": r.get("title", ""),
                    "description": r.get("description", ""),
                }
                for r in ddg_raw
            ]
        except Exception as e:
            logger.debug("DDG dork search failed for %s: %s", platform_domain, e)

    seen_urls = set()
    for res in results[:8]:
        url = res.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        username = extract_username(url)
        platform = detect_platform(url)

        finding = {
            "title": f"Search: {res.get('title', url)[:200]}",
            "detail": res.get("description", "")[:300] or url,
            "source_url": url,
            "source_type": platform_domain.split(".")[0],
            "icon": icon,
            "verified": False,
            "subject_id": subject_id,
            "screenshots": [{"url": None, "source_url": url}],
        }

        if username and platform and platform in MAJOR_SOCIAL_PLATFORMS:
            finding["social_account"] = {
                "platform": platform,
                "username": username,
                "url": url,
            }

        findings.append(finding)

    return findings


def link_finding_to_manual_action(finding_id, case_id, user_id):
    """Link a manually created finding to a 'Manual entry' action for the case."""
    action = WorkflowResearchAction.query.filter_by(
        case_id=case_id,
        action_type="manual_entry",
    ).first()
    if not action:
        action = WorkflowResearchAction(
            id=str(uuid.uuid4()),
            case_id=case_id,
            action_type="manual_entry",
            data_value="",
            label="Manual entry",
            status="completed",
            started_at=datetime.now(),
            completed_at=datetime.now(),
            result_summary="",
            created_by=user_id,
        )
        db.session.add(action)
        db.session.flush()

    existing = WorkflowActionFinding.query.filter_by(
        action_id=action.id, finding_id=finding_id
    ).first()
    if not existing:
        link = WorkflowActionFinding(action_id=action.id, finding_id=finding_id)
        db.session.add(link)
