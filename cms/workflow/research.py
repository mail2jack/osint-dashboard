import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime

from cms.models import db, SocialAccount
from .models import (
    WorkflowCase,
    WorkflowResearchAction,
    WorkflowFinding,
    WorkflowScreenshot,
    WorkflowActionFinding,
)
from cms.services.http_utils import jittered_get
from cms.services.phone_service import (
    _whatsapp_check_internal,
    _telegram_check_internal,
)

logger = logging.getLogger(__name__)

ACTION_REGISTRY = {}
_running_threads = {}
_threads_lock = threading.Lock()
_credit_lock = threading.Lock()

CREDIT_LIMITS = {
    "tiktok": 50,
    "instagram": 10,
    "linkedin": 50,
    "twitter": 1000,
}


def get_remaining_credits(action_type):
    with _credit_lock:
        month = datetime.now().strftime("%Y-%m")
        try:
            from cms.models import Setting

            usage = Setting.get("rapidapi_credits_usage", {})
            used = usage.get(action_type, {}).get(month, 0)
            limit = CREDIT_LIMITS.get(action_type, 0)
            return max(0, limit - used)
        except Exception:
            return CREDIT_LIMITS.get(action_type, 0)


def _use_credit(action_type):
    with _credit_lock:
        month = datetime.now().strftime("%Y-%m")
        try:
            from cms.models import Setting

            usage = Setting.get("rapidapi_credits_usage", {})
            usage.setdefault(action_type, {})
            usage[action_type][month] = usage[action_type].get(month, 0) + 1
            Setting.set("rapidapi_credits_usage", usage)
        except Exception:
            logger.warning(
                "Failed to record credit usage for %s", action_type, exc_info=True
            )


def _has_credits(action_type):
    return get_remaining_credits(action_type) > 0


def register_action(action_type, label, icon, handler, description=""):
    ACTION_REGISTRY[action_type] = {
        "label": label,
        "icon": icon,
        "handler": handler,
        "description": description,
    }


def cancel_action(action_id):
    with _threads_lock:
        if action_id in _running_threads:
            _running_threads[action_id]["cancel"] = True
    action = db.session.get(WorkflowResearchAction, action_id)
    if action and action.status == "running":
        action.status = "cancelled"
        action.completed_at = datetime.now()
        action.result_summary = "Cancelled"
        links = WorkflowActionFinding.query.filter_by(action_id=action_id).all()
        if links:
            finding_ids = [link.finding_id for link in links]
            WorkflowFinding.query.filter(
                WorkflowFinding.id.in_(finding_ids),
            ).delete(synchronize_session=False)
            WorkflowActionFinding.query.filter_by(action_id=action_id).delete()
        db.session.commit()


def is_action_cancelled(action_id):
    with _threads_lock:
        return action_id in _running_threads and _running_threads[action_id].get(
            "cancel"
        )


def run_action(action_id):
    try:
        action = db.session.get(WorkflowResearchAction, action_id)
        if not action:
            return

        action.status = "running"
        action.started_at = datetime.now()
        db.session.commit()

        entry = ACTION_REGISTRY.get(action.action_type)
        if not entry:
            action.status = "error"
            action.error = f"Unknown action type: {action.action_type}"
            db.session.commit()
            return

        # Resolve who created this action for finding.created_by
        action_creator_id = getattr(action, "created_by", None)
        if not action_creator_id:
            case = db.session.get(WorkflowCase, action.case_id)
            action_creator_id = case.created_by if case else None

        prev_actions = WorkflowResearchAction.query.filter(
            WorkflowResearchAction.case_id == action.case_id,
            WorkflowResearchAction.action_type == action.action_type,
            WorkflowResearchAction.id != action.id,
            WorkflowResearchAction.status == "completed",
        ).all()
        if prev_actions:
            prev_ids = [a.id for a in prev_actions]
            old_links = WorkflowActionFinding.query.filter(
                WorkflowActionFinding.action_id.in_(prev_ids)
            ).all()
            old_finding_ids = list(set(link.finding_id for link in old_links))
            if old_finding_ids:
                now = datetime.now()
                WorkflowFinding.query.filter(
                    WorkflowFinding.id.in_(old_finding_ids),
                    WorkflowFinding.archived_at.is_(None),
                ).update({"archived_at": now}, synchronize_session=False)
                db.session.commit()

        findings_data = entry["handler"](action)

        if is_action_cancelled(action_id):
            action.status = "cancelled"
            action.completed_at = datetime.now()
            action.result_summary = "Cancelled"
            db.session.commit()
            return

        created = []
        for fd in findings_data:
            detail_text = fd.get("detail", "")
            subject_id = fd.get("subject_id")
            finding = WorkflowFinding(
                id=str(uuid.uuid4()),
                case_id=action.case_id,
                subject_id=subject_id,
                title=fd["title"],
                content=detail_text or fd["title"],
                detail=detail_text,
                source_url=fd.get("source_url"),
                source_type=fd.get("source_type", action.action_type),
                icon=fd.get("icon", entry["icon"]),
                verified=fd.get("verified", False),
                raw_data=fd.get("raw_data"),
                created_by=action_creator_id,
                created_at=datetime.now(),
            )
            db.session.add(finding)
            db.session.flush()

            link = WorkflowActionFinding(action_id=action.id, finding_id=finding.id)
            db.session.add(link)

            for ss in fd.get("screenshots", []):
                screenshot = WorkflowScreenshot(
                    id=str(uuid.uuid4()),
                    finding_id=finding.id,
                    url=ss.get("url"),
                    source_url=ss.get("source_url"),
                    file_path=ss.get("file_path"),
                    captured_at=datetime.now(),
                )
                db.session.add(screenshot)

            sa_data = fd.get("social_account")
            if sa_data and subject_id:
                dedup = SocialAccount.query.filter(
                    SocialAccount.subject_id == subject_id,
                    SocialAccount.platform == sa_data["platform"],
                    SocialAccount.username == sa_data["username"],
                ).first()
                if not dedup:
                    db.session.add(
                        SocialAccount(
                            subject_id=subject_id,
                            platform=sa_data["platform"],
                            username=sa_data["username"],
                            url=sa_data.get("url"),
                            account_id=sa_data.get("account_id"),
                            finding_id=finding.id,
                        )
                    )

            created.append(finding)

        action.status = "completed"
        action.completed_at = datetime.now()
        action.result_summary = f"{len(created)} findings"
        db.session.commit()

        from cms.services.invoice_service import auto_invoice_action_completed

        auto_invoice_action_completed(action)

    except Exception as e:
        logger.exception("Research action failed: %s", action_id)
        db.session.rollback()
        action = db.session.get(WorkflowResearchAction, action_id)
        if action:
            action.status = "error"
            action.error = str(e)
            db.session.commit()


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


def start_action_async(action_id):
    from flask import current_app

    try:
        _app = current_app._get_current_object()
    except RuntimeError:
        _app = None

    def _run():
        try:
            if _app:
                with _app.app_context():
                    run_action(action_id)
            else:
                run_action(action_id)
        finally:
            with _threads_lock:
                _running_threads.pop(action_id, None)

    t = threading.Thread(target=_run, args=(), daemon=True)
    t.start()
    with _threads_lock:
        _running_threads[action_id] = {"thread": t, "cancel": False}


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
        pass
    return None


# ─── Action Handlers ──────────────────────────────────────


def _first_subject(action):
    subs = action.case.subjects
    return subs.first() if subs else None


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


def _email_check(action):
    email = action.data_value if action.data_value else None
    if not email:
        subject = _first_subject(action)
        email = subject.email if subject else None
    if not email:
        return []
    findings = []
    from cms.email_search import lookup_email

    try:
        result = lookup_email(email)
        confirmed = [
            a for a in result.get("account_checks", []) if a.get("exists") == True
        ]
        unverified = [
            a for a in result.get("account_checks", []) if a.get("exists") is None
        ]
        if confirmed:
            for acct in confirmed:
                site_name = acct.get("name") or acct.get("site") or "unknown"
                findings.append(
                    {
                        "title": f"Account found on {site_name}",
                        "detail": f"Email {email} is registered on {site_name}. "
                        f"Status: {acct.get('status', 'unknown')}",
                        "source_url": acct.get("url"),
                        "source_type": "email",
                        "icon": "📧",
                        "verified": acct.get("verified", False),
                        "screenshots": [{"url": None, "source_url": acct.get("url")}],
                    }
                )
        if unverified:
            findings.append(
                {
                    "title": f"{len(unverified)} site(s) respond but account not confirmed",
                    "detail": f"For {email}, {len(unverified)} site(s) responded with 200 status, "
                    f"but the page contained no confirmation of the account. "
                    f"These are likely false positives.",
                    "source_type": "email",
                    "icon": "⚠️",
                    "verified": False,
                }
            )
        if not confirmed and not unverified:
            findings.append(
                {
                    "title": "No accounts found via email search",
                    "detail": f"No registered accounts found for {email}.",
                    "source_type": "email",
                    "icon": "📧",
                    "verified": False,
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"Email check failed: {e}",
                "detail": str(e),
                "source_type": "email",
                "icon": "📧",
                "verified": False,
            }
        )

    hibp_key = _get_api_key("hibp_api_key")
    if hibp_key:
        try:
            r = jittered_get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                headers={"hibp-api-key": hibp_key, "user-agent": "Iveras-Workflow"},
                timeout=15,
            )
            if r.status_code == 200:
                for breach in r.json():
                    findings.append(
                        {
                            "title": f"Data breach: {breach['Name']}",
                            "detail": f"Domain: {breach.get('Domain', '')}. "
                            f"Data: {', '.join(breach.get('DataClasses', []))}. "
                            f"Date: {breach.get('BreachDate', '')}",
                            "source_url": f"https://haveibeenpwned.com/account/{email}",
                            "source_type": "hibp",
                            "icon": "🔓",
                            "verified": False,
                            "screenshots": [
                                {
                                    "url": None,
                                    "source_url": f"https://haveibeenpwned.com/account/{email}",
                                }
                            ],
                        }
                    )
        except Exception as e:
            logger.warning("HIBP check failed: %s", e)

    # — PGP keyserver check
    try:
        from urllib.parse import quote

        pgp_url = f"https://keys.openpgp.org/vks/v1/by-email/{quote(email)}"
        pgp_resp = jittered_get(pgp_url, timeout=10)
        if pgp_resp.status_code == 200:
            findings.append(
                {
                    "title": "PGP key found",
                    "detail": (
                        f"A PGP public key was found for {email} on "
                        f"keys.openpgp.org. This confirms that the owner "
                        f"uses PGP encryption for email communication."
                    ),
                    "source_url": f"https://keys.openpgp.org/search?q={quote(email)}",
                    "source_type": "pgp",
                    "icon": "🔐",
                    "verified": True,
                    "screenshots": [
                        {
                            "url": None,
                            "source_url": f"https://keys.openpgp.org/search?q={quote(email)}",
                        }
                    ],
                }
            )
    except Exception as e:
        logger.debug("PGP keyserver check failed for %s: %s", email, e)

    # — Brave Search email context
    brave_key = _get_api_key("brave_api_key")
    if brave_key:
        try:
            from cms.services.search_service import brave_search

            ctx_results = brave_search(email, api_key=brave_key)
            for res in ctx_results[:10]:
                findings.append(
                    {
                        "title": f"Mentioned on: {res.get('title', 'unknown')[:200]}",
                        "detail": res.get("description", "")[:300],
                        "source_url": res.get("url"),
                        "source_type": "email_context",
                        "icon": "🔍",
                        "verified": False,
                        "screenshots": [{"url": None, "source_url": res.get("url")}],
                    }
                )
        except Exception as e:
            logger.debug("Brave email context search failed for %s: %s", email, e)
    return findings


def _phone_check(action):
    phone = action.data_value if action.data_value else None
    if not phone:
        subject = _first_subject(action)
        phone = subject.phone if subject else None
    if not phone:
        return []
    findings = []

    import phonenumbers
    from phonenumbers import geocoder, carrier as pn_carrier, timezone as pn_tz

    detail_parts = []
    enrichment = {}

    try:
        parsed = phonenumbers.parse(phone, "NL")
        valid = phonenumbers.is_valid_number(parsed)
        enrichment["valid"] = valid
        detail_parts.append(f"Valid: {'Yes' if valid else 'No'}")

        try:
            region = geocoder.description_for_number(parsed, "nl")
            if region:
                enrichment["region"] = region
                detail_parts.append(f"Region: {region}")
        except Exception:
            pass

        try:
            carrier_name = pn_carrier.name_for_number(parsed, "nl")
            if carrier_name:
                enrichment["carrier"] = carrier_name
                detail_parts.append(f"Carrier: {carrier_name}")
        except Exception:
            pass

        try:
            line_type = pn_carrier._api_for_number(parsed).get("type", "unknown")
            if callable(line_type):
                line_type = line_type(parsed)
            enrichment["line_type"] = str(line_type)
            type_map = {
                0: "Landline",
                1: "Mobile",
                2: "VoIP",
                3: "Personal number",
                5: "Voicemail",
                7: "Satellite",
            }
            label = (
                type_map.get(int(line_type))
                if str(line_type).isdigit()
                else str(line_type)
            )
            detail_parts.append(f"Type: {label or line_type}")
        except Exception:
            pass

        try:
            tz = pn_tz.time_zones_for_number(parsed)
            if tz:
                enrichment["timezone"] = tz[0]
                detail_parts.append(f"Timezone: {tz[0]}")
        except Exception:
            pass

    except Exception:
        pass

    e164 = (
        f"+{parsed.country_code}{parsed.national_number}"
        if "parsed" in dir()
        else phone
    )

    findings.append(
        {
            "title": f"Phone number {phone} — {enrichment.get('valid', True) and 'Valid' or 'Invalid'}",
            "detail": "\n".join(detail_parts)
            if detail_parts
            else f"Phone number: {phone}",
            "source_type": "phone",
            "icon": "📞",
            "verified": bool(enrichment.get("valid")),
        }
    )

    wa = _whatsapp_check_internal(phone)
    if wa.get("exists") is True:
        findings.append(
            {
                "title": "WhatsApp account found",
                "detail": "This number is active on WhatsApp.",
                "source_url": wa.get("url"),
                "source_type": "phone",
                "icon": "💬",
                "verified": False,
            }
        )
    elif wa.get("exists") is False:
        findings.append(
            {
                "title": "No WhatsApp account",
                "detail": "This number was not found on WhatsApp.",
                "source_type": "phone",
                "icon": "💬",
                "verified": False,
            }
        )

    tg = _telegram_check_internal(phone)
    if tg.get("exists") is True:
        findings.append(
            {
                "title": "Telegram account found",
                "detail": "This number is active on Telegram.",
                "source_url": tg.get("url"),
                "source_type": "phone",
                "icon": "✈️",
                "verified": False,
            }
        )

    from cms.services.phone_service import _whatsapp_check_baileys

    ba = _whatsapp_check_baileys(e164)
    if ba.get("on_whatsapp") is True and not ba.get("error"):
        detail_lines = []
        detail_lines.append("Status: Active on WhatsApp")
        if ba.get("is_business"):
            detail_lines.append("Type: Business account")
        else:
            detail_lines.append("Type: Personal account")
        if ba.get("status_text"):
            detail_lines.append(f"Status text: {ba['status_text']}")
        biz = ba.get("business") or {}
        if biz.get("description"):
            detail_lines.append(f"Description: {biz['description']}")
        if biz.get("website"):
            detail_lines.append(f"Website: {', '.join(biz['website'])}")
        if biz.get("email"):
            detail_lines.append(f"Email: {biz['email']}")
        if biz.get("category"):
            detail_lines.append(f"Category: {biz['category']}")
        if biz.get("address"):
            detail_lines.append(f"Address: {biz['address']}")
        if ba.get("profile_pic"):
            detail_lines.append(f"Profile photo: {ba['profile_pic']}")
        if detail_lines:
            findings.append(
                {
                    "title": "WhatsApp Business data"
                    if ba.get("is_business")
                    else "WhatsApp data",
                    "detail": "\n".join(detail_lines),
                    "source_type": "phone",
                    "icon": "🏢" if ba.get("is_business") else "💬",
                    "verified": True,
                    "screenshots": [
                        {
                            "url": ba["profile_pic"],
                            "source_url": None,
                        }
                    ]
                    if ba.get("profile_pic")
                    else [],
                }
            )
    elif ba.get("error"):
        # fall back to 2Chat if available
        try:
            from cms.services.phone_service import _get_twochat_credentials

            api_key, channel_id = _get_twochat_credentials()
            if api_key and channel_id:
                twochat_url = f"https://api.p.2chat.io/open/whatsapp/check-number/{channel_id}/{e164}"
                twochat_headers = {
                    "X-User-API-Key": api_key,
                    "Accept": "application/json",
                }
                twochat_resp = jittered_get(
                    twochat_url, headers=twochat_headers, timeout=30
                )
                if twochat_resp.status_code == 200:
                    tc_data = twochat_resp.json()
                    on_wa = tc_data.get("on_whatsapp")
                    wa_info = tc_data.get("whatsapp_info", {}) or {}
                    biz_info = wa_info.get("business_information", {}) or {}
                    detail_lines = []
                    if on_wa is True:
                        detail_lines.append(
                            "Status: Active on WhatsApp (via 2Chat API)"
                        )
                    elif on_wa is False:
                        detail_lines.append("Status: Not active on WhatsApp")
                    else:
                        detail_lines.append("Status: Unknown")
                    if wa_info.get("verified_level"):
                        detail_lines.append(
                            f"Verified level: {wa_info['verified_level']}"
                        )
                    if wa_info.get("status_text"):
                        detail_lines.append(f"Status text: {wa_info['status_text']}")
                    if wa_info.get("number_id"):
                        detail_lines.append(f"Number ID: {wa_info['number_id']}")
                    region_info = tc_data.get("number", {})
                    if region_info.get("region"):
                        detail_lines.append(f"Region (2Chat): {region_info['region']}")
                    if region_info.get("timezone"):
                        detail_lines.append(
                            f"Timezone (2Chat): {', '.join(region_info['timezone'])}"
                        )
                    if biz_info.get("verified_name"):
                        detail_lines.append(
                            f"Business name: {biz_info['verified_name']}"
                        )
                    if biz_info.get("description"):
                        detail_lines.append(f"Description: {biz_info['description']}")
                    if biz_info.get("website"):
                        detail_lines.append(
                            f"Website: {', '.join(biz_info['website'])}"
                        )
                    if wa_info.get("contact_profile_pic"):
                        detail_lines.append(
                            f"Profile photo: {wa_info['contact_profile_pic']}"
                        )
                    if detail_lines:
                        findings.append(
                            {
                                "title": "WhatsApp Business API data",
                                "detail": "\n".join(detail_lines),
                                "source_type": "phone",
                                "icon": "🏢",
                                "verified": True,
                                "screenshots": [
                                    {
                                        "url": wa_info.get("contact_profile_pic"),
                                        "source_url": None,
                                    }
                                ]
                                if wa_info.get("contact_profile_pic")
                                else [],
                            }
                        )
        except Exception:
            logger.debug("WhatsApp info check failed for %s", phone, exc_info=True)

    return findings


def _address_check(action):
    findings = []
    address_query = action.data_value if action.data_value else None
    if not address_query:
        subject = _first_subject(action)
        parts = []
        if subject:
            if subject.street:
                addr = f"{subject.street} {subject.house_number or ''}{subject.house_number_addition or ''}".strip()
                parts.append(addr)
            if subject.postal_code or subject.city:
                parts.append(
                    f"{subject.postal_code or ''} {subject.city or ''}".strip()
                )
        address_query = ", ".join(parts) if parts else None
    if not address_query:
        findings.append(
            {
                "title": "No address details provided",
                "detail": "Enter street, house number, postal code and city for the subject.",
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
            }
        )
        return findings
    try:
        r = jittered_get(
            "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free",
            params={"q": address_query, "rows": 10},
            timeout=10,
        )
        data = r.json()
        docs = (data.get("response", {}) or {}).get("docs", [])

        # Filter only address-level results, sorted by score descending
        adres_docs = [d for d in docs if d.get("type") == "adres"]
        adres_docs.sort(key=lambda d: d.get("score") or 0, reverse=True)

        for doc in adres_docs[:1]:
            nummeraanduiding = doc.get("nummeraanduiding_id") or doc.get("id", "")
            bag_url = f"https://bagviewer.kadaster.nl/lvbag/bag-viewer/?searchQuery={doc.get('weergavenaam', address_query)}&objectId={nummeraanduiding}&theme=BRT+Achtergrond&zoomlevel=16"

            details = []
            if doc.get("straatnaam"):
                hn = doc.get("huisnummer", "")
                hnl = doc.get("huis_nlt", "")
                hn_display = hnl if hnl else hn
                details.append(f"Address: {doc['straatnaam']} {hn_display}")
            if doc.get("postcode"):
                details.append(f"Postal code: {doc['postcode']}")
            if doc.get("woonplaatsnaam"):
                details.append(f"City: {doc['woonplaatsnaam']}")
            if doc.get("buurtnaam"):
                details.append(f"Neighborhood: {doc['buurtnaam']}")
            if doc.get("wijknaam"):
                details.append(f"District: {doc['wijknaam']}")
            if doc.get("gemeentenaam"):
                details.append(f"Municipality: {doc['gemeentenaam']}")
            if doc.get("provincienaam"):
                details.append(f"Province: {doc['provincienaam']}")
            if doc.get("gekoppeld_perceel"):
                percelen = "; ".join(doc["gekoppeld_perceel"])
                details.append(f"Cadastral parcel: {percelen}")
            if doc.get("gekoppeld_appartement"):
                apps = "; ".join(doc["gekoppeld_appartement"])
                details.append(f"Apartment right: {apps}")
            if doc.get("openbareruimtetype"):
                details.append(f"Public space type: {doc['openbareruimtetype']}")

            findings.append(
                {
                    "title": f"Adres: {doc.get('weergavenaam', address_query)}",
                    "detail": "\n".join(details)
                    if details
                    else "BAG registration found.",
                    "source_url": bag_url,
                    "source_type": "kadaster",
                    "icon": "🏠",
                    "verified": False,
                    "screenshots": [{"url": None, "source_url": bag_url}],
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"Address check failed: {e}",
                "detail": str(e),
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
            }
        )
    if not findings:
        findings.append(
            {
                "title": f"Adres: {address_query}",
                "detail": "Kadaster lookup returned no results. Possibly not found in BAG.",
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
            }
        )
    return findings


def _social_scan(action):
    findings = []
    from cms.social_extractor import detect_platform, MAJOR_SOCIAL_PLATFORMS
    from cms.username_search import search_username, search_username_maigret

    # Collect accounts with their subject context
    accounts_with_subject = []

    if action.data_value:
        try:
            raw = json.loads(action.data_value)
            if isinstance(raw, list):
                for item in raw:
                    subject_id = None
                    if isinstance(item, dict):
                        subject_id = item.get("subject_id")
                    accounts_with_subject.append((item, subject_id))
        except (json.JSONDecodeError, TypeError):
            pass

    if not accounts_with_subject:
        for subject in action.case.subjects or []:
            sa_query = getattr(subject, "social_accounts", None)
            if sa_query is not None:
                try:
                    count = sa_query.count()
                except Exception:
                    count = 0
                if count > 0:
                    for sa in sa_query:
                        accounts_with_subject.append((sa, subject.id))
            if subject.name:
                accounts_with_subject.append((subject.name, subject.id))

    if not accounts_with_subject:
        findings.append(
            {
                "title": "No social media accounts to scan",
                "detail": "No social media accounts have been provided for this subject.",
                "source_type": "social",
                "icon": "🌐",
                "verified": False,
            }
        )
        return findings

    for acct, subject_id in accounts_with_subject:
        username = None
        label = None
        url = None
        input_platform = None

        if isinstance(acct, str):
            username = acct.strip()
            if username.startswith("@"):
                username = username[1:]
            label = username
        else:
            url = (
                getattr(acct, "url", None)
                if not isinstance(acct, dict)
                else acct.get("url")
            )
            input_platform = (
                getattr(acct, "platform", None)
                if not isinstance(acct, dict)
                else acct.get("platform")
            )
            username = (
                getattr(acct, "username", None)
                if not isinstance(acct, dict)
                else acct.get("username")
            )
            if not username and url:
                username = url.rstrip("/").split("/")[-1]
            label = input_platform or username or "unknown"

        if not username:
            continue

        seen_sites = set()

        try:
            maigret_result = search_username_maigret(username)
            if maigret_result.get("found_count", 0) > 0:
                for f in maigret_result.get("findings", []):
                    site = f.get("site") or f.get("platform", "")
                    if f.get("exists") == True and site not in seen_sites:
                        seen_sites.add(site)
                        result_url = f.get("url", "")
                        platform = detect_platform(result_url)
                        finding = {
                            "title": f"{label}: profile active ({site})",
                            "detail": f"Found via Maigret. URL: {result_url}",
                            "source_url": result_url,
                            "source_type": "social",
                            "icon": "🌐",
                            "verified": False,
                            "subject_id": subject_id,
                            "screenshots": [{"url": None, "source_url": result_url}],
                        }
                        if platform and platform in MAJOR_SOCIAL_PLATFORMS:
                            finding["social_account"] = {
                                "platform": platform,
                                "username": username,
                                "url": result_url,
                            }
                        findings.append(finding)
        except Exception as e:
            logger.warning("Maigret search for %s failed: %s", username, e)

        try:
            sherlock_result = search_username(username)
            if sherlock_result.get("found_count", 0) > 0:
                for f in sherlock_result.get("findings", []):
                    site = f.get("platform") or f.get("site", "")
                    if f.get("exists") == True and site not in seen_sites:
                        seen_sites.add(site)
                        result_url = f.get("url", "")
                        platform = detect_platform(result_url)
                        finding = {
                            "title": f"{label}: profile active ({site})",
                            "detail": f"Found via Sherlock. URL: {result_url}",
                            "source_url": result_url,
                            "source_type": "social",
                            "icon": "🌐",
                            "verified": False,
                            "subject_id": subject_id,
                            "screenshots": [{"url": None, "source_url": result_url}],
                        }
                        if platform and platform in MAJOR_SOCIAL_PLATFORMS:
                            finding["social_account"] = {
                                "platform": platform,
                                "username": username,
                                "url": result_url,
                            }
                        findings.append(finding)
        except Exception as e:
            logger.warning("Sherlock search for %s failed: %s", username, e)

    if not findings:
        for acct, subject_id in accounts_with_subject:
            if isinstance(acct, str):
                findings.append(
                    {
                        "title": f"Social media: {acct}",
                        "detail": f"Username: {acct}",
                        "source_type": "social",
                        "icon": "🌐",
                        "verified": False,
                        "subject_id": subject_id,
                    }
                )
            else:
                pl = (
                    getattr(acct, "platform", None)
                    if not isinstance(acct, dict)
                    else acct.get("platform", "unknown")
                )
                u = (
                    getattr(acct, "url", None)
                    if not isinstance(acct, dict)
                    else acct.get("url")
                )
                findings.append(
                    {
                        "title": f"Social media: {pl}",
                        "detail": f"URL: {u}" if u else f"Platform: {pl}",
                        "source_url": u,
                        "source_type": "social",
                        "icon": "🌐",
                        "verified": False,
                        "subject_id": subject_id,
                    }
                )

    return findings


def _kvk_check(action):
    findings = []
    query = action.data_value if action.data_value else None
    if not query:
        subject = _first_subject(action)
        query = subject.name if subject else ""
    if not query:
        return findings

    api_key = _get_api_key("overheid_api_key")
    if api_key:
        try:
            r = jittered_get(
                "https://api.overheid.io/openkvk/zoeken",
                params={"q": query, "rows": 5},
                headers={"ovio-api-key": api_key},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                for item in data if isinstance(data, list) else data.get("data", []):
                    findings.append(
                        {
                            "title": f"KvK: {item.get('naam', 'unknown')}",
                            "detail": f"KvK: {item.get('kvkNummer', '')}. "
                            f"Address: {item.get('straat', '')} {item.get('huisnummer', '')}, "
                            f"{item.get('postcode', '')} {item.get('plaats', '')}. "
                            f"Legal form: {item.get('rechtsvorm', '')}",
                            "source_url": f"https://www.kvk.nl/zoeken/?q={item.get('kvkNummer', query)}",
                            "source_type": "kvk",
                            "icon": "🏢",
                            "verified": False,
                            "screenshots": [
                                {
                                    "url": None,
                                    "source_url": f"https://www.kvk.nl/zoeken/?q={item.get('kvkNummer', query)}",
                                }
                            ],
                        }
                    )
                return findings
            else:
                logger.warning(
                    f"KvK API gaf status {r.status_code}, vallen terug op openkvk.nl"
                )
        except Exception as e:
            logger.warning(f"KvK API exceptie: {e}, vallen terug op openkvk.nl")

    # Fallback: scrape openkvk.nl (geen API key nodig)
    try:
        import html as _html

        r = jittered_get(
            "https://openkvk.nl/search",
            params={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            timeout=15,
        )
        if r.status_code != 200:
            findings.append(
                {
                    "title": "KvK lookup openkvk.nl failed",
                    "detail": f"Status {r.status_code}",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                }
            )
            return findings

        snap_match = re.search(r'wire:snapshot="([^"]+)"', r.text)
        if not snap_match:
            findings.append(
                {
                    "title": "KvK lookup via openkvk.nl",
                    "detail": "No results found on openkvk.nl.",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                }
            )
            return findings

        snap_json = _html.unescape(snap_match.group(1))
        snap_data = json.loads(snap_json)
        companies_data = snap_data.get("data", {}).get("companiesData", [])
        if not companies_data or not companies_data[0]:
            findings.append(
                {
                    "title": "KvK lookup via openkvk.nl",
                    "detail": "No companies found.",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                }
            )
            return findings

        seen_kvk = set()
        for pair in companies_data[0][:5]:
            company = pair[0] if isinstance(pair, list) else pair
            kvk_nummer = company.get("kvknummer", "")
            if kvk_nummer and kvk_nummer in seen_kvk:
                continue
            if kvk_nummer:
                seen_kvk.add(kvk_nummer)

            naam = company.get("naam", "unknown")
            loc = company.get("bezoeklocatie", [{}])
            loc = loc[0] if isinstance(loc, list) and loc else loc
            straat = loc.get("straat", "") if isinstance(loc, dict) else ""
            huisnr = loc.get("huisnummer", "") if isinstance(loc, dict) else ""
            postcode = loc.get("postcode", "") if isinstance(loc, dict) else ""
            plaats = loc.get("plaats", "") if isinstance(loc, dict) else ""
            rechtsvorm = company.get("rechtsvormOmschrijving", "")
            handelsnamen = company.get("huidigeHandelsNamen", [])
            if isinstance(handelsnamen, list) and handelsnamen:
                handelsnamen = [h for h in handelsnamen if isinstance(h, str)]
            extra = ""
            if handelsnamen and len(handelsnamen) > 1:
                extra = f"Trade names: {', '.join(handelsnamen[:5])}."

            detail_parts = []
            if kvk_nummer:
                detail_parts.append(f"KvK: {kvk_nummer}")
            addr = " ".join(p for p in [straat, huisnr] if p)
            if addr and postcode and plaats:
                detail_parts.append(f"{addr}, {postcode} {plaats}")
            if rechtsvorm:
                detail_parts.append(rechtsvorm)
            if extra:
                detail_parts.append(extra)
            detail = ". ".join(detail_parts)

            findings.append(
                {
                    "title": f"KvK: {naam}",
                    "detail": detail,
                    "source_url": f"https://openkvk.nl/search?q={kvk_nummer or query}",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                    "screenshots": [
                        {
                            "url": None,
                            "source_url": f"https://www.kvk.nl/zoeken/?q={kvk_nummer or query}",
                        }
                    ],
                }
            )

    except Exception as e:
        findings.append(
            {
                "title": "KvK lookup via openkvk.nl failed",
                "detail": str(e),
                "source_type": "kvk",
                "icon": "🏢",
                "verified": False,
            }
        )

    return findings


def _rdw_check(action):
    findings = []
    ipc = action.data_value if action.data_value else None
    if not ipc:
        subject = _first_subject(action)
        ipc = getattr(subject, "identification_number", None) if subject else None
    if not ipc:
        findings.append(
            {
                "title": "No license plate provided for RDW check",
                "detail": "Add a license plate to the subject via the license plate field.",
                "source_type": "rdw",
                "icon": "🚗",
                "verified": False,
            }
        )
        return findings
    try:
        kenteken = ipc.replace("-", "").replace(" ", "").upper()
        r = jittered_get(
            "https://opendata.rdw.nl/resource/m9d7-ebf2.json",
            params={"kenteken": kenteken},
            timeout=15,
        )
        if r.status_code == 200 and r.json():
            data = dict(r.json()[0])

            def _fmt(v):
                return (
                    v.replace("-", "").replace("T", " ")[:10]
                    if v and ("-" in v or "T" in v)
                    else v
                )

            try:
                prijs = data.get("catalogusprijs", "")
                if prijs:
                    data["_prijs_eur"] = f"€ {int(prijs):,}".replace(",", ".")
            except Exception:
                pass
            detail_parts = []
            if data.get("kenteken"):
                detail_parts.append(f"License plate: {data['kenteken']}")
            if data.get("eerste_kleur"):
                detail_parts.append(f"Color: {data['eerste_kleur']}")
            if data.get("vermogen_massario"):
                detail_parts.append(f"{data['vermogen_massario']} kW")
            if data.get("brandstof_omschrijving"):
                detail_parts.append(data["brandstof_omschrijving"])
            if data.get("vervaldatum_apk"):
                detail_parts.append(f"APK: {_fmt(data['vervaldatum_apk'])}")
            findings.append(
                {
                    "title": f"🚗 {data.get('merk', 'unknown')} {data.get('handelsbenaming', '')}",
                    "detail": " · ".join(detail_parts),
                    "source_type": "rdw",
                    "icon": "🚗",
                    "verified": False,
                    "raw_data": data,
                }
            )
        else:
            findings.append(
                {
                    "title": f"No RDW data for license plate {kenteken}",
                    "detail": "Vehicle not found in RDW registration.",
                    "source_type": "rdw",
                    "icon": "🚗",
                    "verified": False,
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"RDW check failed: {e}",
                "detail": str(e),
                "source_type": "rdw",
                "icon": "🚗",
                "verified": False,
            }
        )
    return findings


def _vessel_check(action):
    findings = []
    from cms.encryption_utils import encryptor

    identifier = action.data_value if action.data_value else None
    subject = _first_subject(action)

    imo = mmsi = eni = name = None
    if identifier:
        cleaned = identifier.strip()
        if re.match(r"^(IMO\s*)?\d{7}$", cleaned, re.IGNORECASE):
            imo = re.sub(r"(?i)^IMO\s*", "", cleaned)
        elif re.match(r"^\d{9}$", cleaned):
            mmsi = cleaned
        elif re.match(r"^\d{8,9}$", cleaned):
            eni = cleaned
        else:
            name = cleaned
        if subject:
            name = name or subject.name
    else:
        if subject and subject.subject_type == "vessel":
            try:
                imo = (
                    encryptor.decrypt(subject.imo_number)
                    if subject.imo_number
                    else None
                )
            except Exception:
                imo = None
            try:
                mmsi = encryptor.decrypt(subject.mmsi) if subject.mmsi else None
            except Exception:
                mmsi = None
            try:
                eni = (
                    encryptor.decrypt(subject.eni_number)
                    if subject.eni_number
                    else None
                )
            except Exception:
                eni = None
            name = subject.name

            # Fallback to identification_number (workflow IMO-veld)
            if not imo and subject.identification_number:
                raw = subject.identification_number
                try:
                    raw = encryptor.decrypt(raw)
                except Exception:
                    logger.warning(
                        "Failed to decrypt identification_number for subject %s",
                        subject.id,
                        exc_info=True,
                    )
                    raw = ""
                if re.match(r"^\d{7}$", raw.strip()):
                    imo = raw.strip()
                else:
                    name = name or raw

            # Fallback to vessel_data
            if not any([imo, mmsi, eni]) and subject.vessel_data:
                vd = subject.vessel_data
                imo = imo or vd.get("imo")
                mmsi = mmsi or vd.get("mmsi")
                eni = eni or vd.get("eni")
                name = name or vd.get("name")

    if not any([imo, mmsi, eni, name]):
        findings.append(
            {
                "title": "No vessel data provided",
                "detail": "Enter an IMO, MMSI, ENI number or vessel name, "
                "or link a vessel subject to this investigation first.",
                "source_type": "vessel",
                "icon": "🚢",
                "verified": False,
            }
        )
        return findings

    try:
        from cms.vessel_service import lookup_vessel

        result = lookup_vessel(imo=imo, mmsi=mmsi, eni=eni, name=name)

        if result.get("found"):
            parts = []
            if result.get("name"):
                parts.append(f"Name: {result['name']}")
            if result.get("imo"):
                parts.append(f"IMO: {result['imo']}")
            if result.get("mmsi"):
                parts.append(f"MMSI: {result['mmsi']}")
            if result.get("eni"):
                parts.append(f"ENI: {result['eni']}")
            if result.get("flag"):
                parts.append(f"Flag: {result['flag']}")
            if result.get("ship_type"):
                parts.append(f"Type: {result['ship_type']}")
            if result.get("length"):
                parts.append(f"Length: {result['length']} m")
            if result.get("beam"):
                parts.append(f"Beam: {result['beam']} m")
            if result.get("year_built"):
                parts.append(f"Year built: {result['year_built']}")
            if result.get("callsign"):
                parts.append(f"Callsign: {result['callsign']}")
            if result.get("destination"):
                parts.append(f"Destination: {result['destination']}")
            if result.get("builder"):
                parts.append(f"Builder: {result['builder']}")
            if result.get("position"):
                pos = result["position"]
                lat, lon = pos.get("lat", "?"), pos.get("lon", "?")
                parts.append(
                    f"Position: {lat}, {lon} (https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12)"
                )
            if result.get("position_text"):
                from urllib.parse import quote

                map_q = quote(result["position_text"])
                parts.append(
                    f"Position: {result['position_text']} (https://www.google.com/maps?q={map_q})"
                )
            if result.get("speed"):
                parts.append(f"Speed: {result['speed']} knots")
            if result.get("course"):
                parts.append(f"Course: {result['course']}°")
            if result.get("navigation_status"):
                parts.append(f"Status: {result['navigation_status']}")
            if result.get("draught"):
                parts.append(f"Draught: {result['draught']} m")
            if result.get("eta"):
                parts.append(f"ETA: {result['eta']}")

            sources = result.get("sources", [])
            if sources:
                parts.append(f"\nSources: {', '.join(sources)}")

            findings.append(
                {
                    "title": f"🚢 {result.get('name', 'Unknown vessel')}",
                    "detail": " · ".join(parts) if parts else "Data found",
                    "source_type": "vessel",
                    "icon": "🚢",
                    "verified": False,
                    "raw_data": {
                        "imo": result.get("imo"),
                        "mmsi": result.get("mmsi"),
                        "eni": result.get("eni"),
                        "name": result.get("name"),
                        "flag": result.get("flag"),
                        "ship_type": result.get("ship_type"),
                        "length": result.get("length"),
                        "beam": result.get("beam"),
                        "year_built": result.get("year_built"),
                        "callsign": result.get("callsign"),
                        "destination": result.get("destination"),
                        "builder": result.get("builder"),
                        "position": result.get("position"),
                        "position_text": result.get("position_text"),
                        "speed": result.get("speed"),
                        "course": result.get("course"),
                        "navigation_status": result.get("navigation_status"),
                        "eta": result.get("eta"),
                        "draught": result.get("draught"),
                        "sources": sources,
                        "source_data": result.get("source_data", {}),
                    },
                }
            )
        else:
            findings.append(
                {
                    "title": "No vessel data found",
                    "detail": "No maritime sources yielded data for the provided identifiers.",
                    "source_type": "vessel",
                    "icon": "🚢",
                    "verified": False,
                }
            )
    except Exception as e:
        logger.exception("Vessel check failed")
        findings.append(
            {
                "title": f"Vessel check failed: {e}",
                "detail": str(e),
                "source_type": "vessel",
                "icon": "🚢",
                "verified": False,
            }
        )
    return findings


def _osint_deep_search(action):
    findings = []
    name = action.data_value if action.data_value else None
    subject = _first_subject(action)
    if not name:
        name = getattr(subject, "name", None) if subject else None
    subject_id = subject.id if subject else None
    if not name:
        return findings
    brave_key = _get_api_key("brave_api_key")
    from cms.social_extractor import (
        detect_platform,
        extract_username,
        MAJOR_SOCIAL_PLATFORMS,
    )
    from cms.services.search_service import brave_search

    try:
        results = brave_search(name, api_key=brave_key)
        for res in results[:10]:
            url = res.get("url") or ""
            platform = detect_platform(url) if url else None
            finding = {
                "title": f"OSINT: {res.get('title', 'unknown')[:200]}",
                "detail": res.get("description", "")[:300],
                "source_url": url,
                "source_type": "osint",
                "icon": "🌍",
                "verified": False,
                "subject_id": subject_id,
                "screenshots": [{"url": None, "source_url": url}],
            }
            if platform and platform in MAJOR_SOCIAL_PLATFORMS:
                username = extract_username(url, platform=platform)
                if username:
                    finding["social_account"] = {
                        "platform": platform,
                        "username": username,
                        "url": url,
                    }
            findings.append(finding)
    except Exception as e:
        findings.append(
            {
                "title": f"OSINT deep search error: {e}",
                "detail": str(e),
                "source_type": "osint",
                "icon": "🌍",
                "verified": False,
                "subject_id": subject_id,
            }
        )
    if not findings:
        try:
            from cms.services.search_service import person_dorks_search

            dork_results = person_dorks_search(name)
            for link in dork_results.get("search_links", [])[:10]:
                url = link.get("url") or ""
                platform = detect_platform(url) if url else None
                finding = {
                    "title": f"OSINT: {link.get('title', 'result')[:200]}",
                    "detail": link.get("snippet", "")[:300],
                    "source_url": url,
                    "source_type": "osint",
                    "icon": "🌍",
                    "verified": False,
                    "subject_id": subject_id,
                    "screenshots": [{"url": None, "source_url": url}],
                }
                if platform and platform in MAJOR_SOCIAL_PLATFORMS:
                    username = extract_username(url, platform=platform)
                    if username:
                        finding["social_account"] = {
                            "platform": platform,
                            "username": username,
                            "url": url,
                        }
                findings.append(finding)
        except Exception as e:
            logger.warning("Dork search failed: %s", e)
    return findings


def _financial_check(action):
    """Placeholder — financieel onderzoek is handmatig in deze fase."""
    return [
        {
            "title": "Financial research — manual action",
            "detail": "Financial research requires manual request to banks/supervisors. "
            "This action serves as a reminder to request bank statements and transaction overviews "
            "from the relevant institutions.",
            "source_type": "financial",
            "icon": "💰",
            "verified": False,
        }
    ]


def _facebook_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")

    query = action.data_value if action.data_value else None
    subject = _first_subject(action)
    if not query:
        query = subject.name if subject else ""
    if not query:
        return findings

    subject_id = subject.id if subject else None
    name_for_dork = subject.name if subject else query

    # ─── Phase 1: Brave/DDG dork search (always runs) ──────
    dork_findings = _site_dork_search(
        "facebook.com", name_for_dork, subject_id, icon="📘"
    )
    seen_urls = set()
    for f in dork_findings:
        url = f.get("source_url")
        if url:
            seen_urls.add(url)
    findings.extend(dork_findings)

    # ─── Phase 2: API enrichment (only if key available) ────
    if not api_key:
        return findings

    is_url = query.startswith("http://") or query.startswith("https://")
    is_username = (
        not is_url and "/" not in query and " " not in query and len(query) < 100
    )

    def add_api_finding(name, url, detail=""):
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        findings.append(
            {
                "title": f"Facebook: {name}",
                "detail": detail or url or name,
                "source_url": url or "",
                "source_type": "facebook",
                "icon": "📘",
                "verified": False,
                "screenshots": [{"url": None, "source_url": url}] if url else [],
            }
        )

    # ─── PullAPI (primary for URL/username) ────────────────
    if is_url or is_username:
        username_param = query
        if is_url:
            m = re.search(r"facebook\.com/(?:profile\.php\?id=)?([^/?&#]+)", query)
            if m:
                username_param = m.group(1)
        try:
            r = jittered_get(
                "https://facebook-scraper-api9.p.rapidapi.com/facebook/profile",
                params={"username": username_param},
                headers={
                    "x-rapidapi-key": api_key,
                    "x-rapidapi-host": "facebook-scraper-api9.p.rapidapi.com",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("data") if isinstance(body, dict) else body
                if data and data.get("name"):
                    name = data.get("name", "")
                    url = (
                        data.get("profile_url", "")
                        or f"https://www.facebook.com/{username_param}"
                    )
                    detail_parts = []
                    if data.get("location"):
                        detail_parts.append(f"📍 {data['location']}")
                    if data.get("follower_count") is not None:
                        detail_parts.append(f"👥 {data['follower_count']:,} followers")
                    if data.get("bio"):
                        bp = (
                            data["bio"][:120] + "…"
                            if len(data["bio"]) > 120
                            else data["bio"]
                        )
                        detail_parts.append(f"📝 {bp}")
                    if data.get("is_verified"):
                        detail_parts.append("✅ Verified")
                    add_api_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code in (401, 403):
                logger.warning("PullAPI auth error — falling back to Scraper3")
            elif r.status_code == 429:
                logger.warning("PullAPI rate limit — falling back to Scraper3")
            else:
                logger.warning(
                    "PullAPI returned %s — falling back to Scraper3", r.status_code
                )
        except Exception as e:
            logger.warning("PullAPI profile failed: %s — falling back to Scraper3", e)

    # ─── Scraper3 fallback ─────────────────────────────────
    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "facebook-scraper3.p.rapidapi.com",
            "Accept": "application/json",
        }

        def api_get(path, params):
            r = jittered_get(
                f"https://facebook-scraper3.p.rapidapi.com{path}",
                params=params,
                headers=headers,
                timeout=15,
            )
            if r.status_code in (401, 403, 429):
                return None
            if r.status_code != 200:
                return None
            try:
                return r.json()
            except Exception:
                return None

        def parse_items(data):
            items = (
                data
                if isinstance(data, list)
                else data.get("results") or data.get("data") or data.get("items") or []
            )
            if not items and isinstance(data, dict) and not data.get("error"):
                items = [data]
            return items

        def extract_results(item):
            name = (
                item.get("name")
                or item.get("full_name")
                or item.get("title")
                or item.get("username")
                or item.get("page_name")
                or ""
            )
            url = (
                item.get("url")
                or item.get("link")
                or item.get("profile_url")
                or item.get("permalink")
                or item.get("page_url")
                or ""
            )
            location = (
                item.get("location") or item.get("city") or item.get("country") or ""
            )
            mutual = (
                item.get("mutual_friends")
                or item.get("friends_count")
                or item.get("followers")
                or ""
            )
            bio = item.get("bio") or item.get("about") or item.get("description") or ""
            return name, url, location, mutual, bio

        def process_profile(data):
            items = parse_items(data)[:10]
            for item in items:
                if not isinstance(item, dict):
                    continue
                name, url, location, mutual, bio = extract_results(item)
                if not name:
                    continue
                detail_parts = []
                if location:
                    detail_parts.append(f"📍 {location}")
                if mutual:
                    detail_parts.append(f"👥 {mutual}")
                if bio:
                    bp = bio[:120] + "…" if len(bio) > 120 else bio
                    detail_parts.append(f"📝 {bp}")
                detail = " · ".join(detail_parts)
                add_api_finding(name, url, detail)

        if is_url:
            for ep in ["/profile/details_url", "/page/details"]:
                data = api_get(ep, {"url": query})
                if data:
                    name = (
                        data.get("name")
                        or data.get("full_name")
                        or data.get("title")
                        or ""
                    )
                    url = (
                        data.get("url")
                        or data.get("link")
                        or data.get("page_url")
                        or query
                    )
                    loc = data.get("location") or data.get("city") or ""
                    mutual = data.get("mutual_friends") or data.get("followers") or ""
                    bio = (
                        data.get("bio")
                        or data.get("about")
                        or data.get("description")
                        or ""
                    )
                    detail_parts = []
                    if loc:
                        detail_parts.append(f"📍 {loc}")
                    if mutual:
                        detail_parts.append(f"👥 {mutual}")
                    if bio:
                        detail_parts.append(
                            f"📝 {bio[:120]}{'…' if len(bio) > 120 else ''}"
                        )
                    add_api_finding(name or query, url, " · ".join(detail_parts))
                    break

        elif is_username:
            fb_url = f"https://www.facebook.com/{query}"
            data = api_get("/profile/details_url", {"url": fb_url})
            if data:
                name = (
                    data.get("name")
                    or data.get("full_name")
                    or data.get("title")
                    or query
                )
                loc = data.get("location") or data.get("city") or ""
                mutual = data.get("mutual_friends") or data.get("followers") or ""
                bio = (
                    data.get("bio")
                    or data.get("about")
                    or data.get("description")
                    or ""
                )
                detail_parts = []
                if loc:
                    detail_parts.append(f"📍 {loc}")
                if mutual:
                    detail_parts.append(f"👥 {mutual}")
                if bio:
                    detail_parts.append(
                        f"📝 {bio[:120]}{'…' if len(bio) > 120 else ''}"
                    )
                add_api_finding(name or query, fb_url, " · ".join(detail_parts))
            else:
                process_profile(
                    api_get("/search/people", {"query": query, "country": "NL"})
                )

        else:
            for ep_path, ep_params in [
                ("/search/people", {"query": query, "country": "NL"}),
                ("/search/pages", {"query": query, "country": "NL"}),
            ]:
                data = api_get(ep_path, ep_params)
                if data:
                    process_profile(data)

    except Exception as e:
        logger.warning("Facebook API check failed: %s", e)

    return findings


def _tiktok_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")

    query = action.data_value if action.data_value else None
    subject = _first_subject(action)
    if not query:
        query = subject.name if subject else ""
    if not query:
        return findings

    subject_id = subject.id if subject else None
    name_for_dork = subject.name if subject else query

    # ─── Phase 1: Brave/DDG dork search (always runs) ──────
    dork_findings = _site_dork_search(
        "tiktok.com", name_for_dork, subject_id, icon="🎵"
    )
    seen_urls = set()
    for f in dork_findings:
        url = f.get("source_url")
        if url:
            seen_urls.add(url)
    findings.extend(dork_findings)

    # ─── Phase 2: API enrichment (only if key + credits) ────
    if not api_key or not _has_credits("tiktok"):
        return findings

    def add_api_finding(name, url, detail=""):
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        findings.append(
            {
                "title": f"TikTok: {name}",
                "detail": detail or url or name,
                "source_url": url or "",
                "source_type": "tiktok",
                "icon": "🎵",
                "verified": False,
                "screenshots": [{"url": None, "source_url": url}] if url else [],
            }
        )

    is_username = "/" not in query and " " not in query and len(query) < 100

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "scraptik.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = jittered_get(
                "https://scraptik.p.rapidapi.com/get-user",
                params={"username": query},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("user") if isinstance(body, dict) else body
                if data and data.get("nickname"):
                    _use_credit("tiktok")
                    name = (
                        data.get("nickname", "") or data.get("unique_id", "") or query
                    )
                    url = f"https://www.tiktok.com/@{data.get('unique_id', query)}"
                    detail_parts = []
                    if data.get("signature"):
                        bp = (
                            data["signature"][:120] + "…"
                            if len(data["signature"]) > 120
                            else data["signature"]
                        )
                        detail_parts.append(f"📝 {bp}")
                    if data.get("follower_count") is not None:
                        detail_parts.append(f"👥 {data['follower_count']:,} followers")
                    if data.get("following_count") is not None:
                        detail_parts.append(f"↗ {data['following_count']:,} following")
                    if data.get("verification_type") or data.get("verified"):
                        detail_parts.append("✅ Verified")
                    add_api_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code == 429:
                logger.warning("TikTok API rate limit")
                return findings
            elif r.status_code in (401, 403):
                logger.warning("TikTok API auth error")
                return findings

        # Name search fallback
        r = jittered_get(
            "https://scraptik.p.rapidapi.com/search-users",
            params={"keyword": query, "count": "5"},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            _use_credit("tiktok")
            body = r.json()
            items = body.get("user_list") if isinstance(body, dict) else body
            if isinstance(items, list):
                for item in items[:5]:
                    if not isinstance(item, dict):
                        continue
                    info = item.get("user_info") or item
                    name = (
                        info.get("nickname")
                        or info.get("unique_id")
                        or info.get("username")
                        or ""
                    )
                    uid = info.get("unique_id") or info.get("username") or ""
                    if not name:
                        continue
                    url = f"https://www.tiktok.com/@{uid}" if uid else ""
                    bio = info.get("signature") or ""
                    detail = (
                        f"📝 {bio[:120]}{'…' if len(bio) > 120 else ''}" if bio else ""
                    )
                    add_api_finding(name, url, detail)

    except Exception as e:
        logger.warning("TikTok API check failed: %s", e)

    return findings


def _instagram_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")

    query = action.data_value if action.data_value else None
    subject = _first_subject(action)
    if not query:
        query = subject.name if subject else ""
    if not query:
        return findings

    subject_id = subject.id if subject else None
    name_for_dork = subject.name if subject else query

    # ─── Phase 1: Brave/DDG dork search (always runs) ──────
    dork_findings = _site_dork_search(
        "instagram.com", name_for_dork, subject_id, icon="📸"
    )
    seen_urls = set()
    for f in dork_findings:
        url = f.get("source_url")
        if url:
            seen_urls.add(url)
    findings.extend(dork_findings)

    # ─── Phase 2: API enrichment (only if key + credits) ────
    if not api_key or not _has_credits("instagram"):
        return findings

    def add_api_finding(name, url, detail=""):
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        findings.append(
            {
                "title": f"Instagram: {name}",
                "detail": detail or url or name,
                "source_url": url or "",
                "source_type": "instagram",
                "icon": "📸",
                "verified": False,
                "screenshots": [{"url": None, "source_url": url}] if url else [],
            }
        )

    is_username = "/" not in query and " " not in query and len(query) < 100

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "pro-social.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = jittered_get(
                "https://pro-social.p.rapidapi.com/userinfo_username/",
                params={"username": query},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("user") if isinstance(body, dict) else body
                if data and (data.get("full_name") or data.get("username")):
                    name = data.get("full_name", "") or data.get("username", query)
                    url = f"https://www.instagram.com/{data.get('username', query)}/"
                    detail_parts = []
                    if data.get("biography"):
                        bp = (
                            data["biography"][:120] + "…"
                            if len(data["biography"]) > 120
                            else data["biography"]
                        )
                        detail_parts.append(f"📝 {bp}")
                    if data.get("follower_count") is not None:
                        detail_parts.append(f"👥 {data['follower_count']:,} followers")
                    if data.get("following_count") is not None:
                        detail_parts.append(f"↗ {data['following_count']:,} following")
                    if data.get("is_verified"):
                        detail_parts.append("✅ Verified")
                    if data.get("business_category_name"):
                        detail_parts.append(f"🏢 {data['business_category_name']}")
                    _use_credit("instagram")
                    add_api_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code == 429:
                logger.warning("Instagram API rate limit")
                return findings
            elif r.status_code in (401, 403):
                logger.warning("Instagram API auth error")
                return findings

        # Name search fallback
        r = jittered_get(
            "https://pro-social.p.rapidapi.com/usersearch/",
            params={"query": query},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            _use_credit("instagram")
            body = r.json()
            users = body.get("users", []) if isinstance(body, dict) else []
            if isinstance(users, dict):
                users = users.get("users") or users.get("results") or []
            for item in users[:5]:
                if not isinstance(item, dict):
                    continue
                name = item.get("full_name") or item.get("username") or ""
                uname = item.get("username") or ""
                if not name:
                    continue
                url = f"https://www.instagram.com/{uname}/" if uname else ""
                bio = item.get("biography") or ""
                detail = f"📝 {bio[:120]}{'…' if len(bio) > 120 else ''}" if bio else ""
                add_api_finding(name, url, detail)

    except Exception as e:
        logger.warning("Instagram API check failed: %s", e)

    return findings


def _linkedin_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")

    query = action.data_value if action.data_value else None
    subject = _first_subject(action)
    if not query:
        query = subject.name if subject else ""
    if not query:
        return findings

    subject_id = subject.id if subject else None
    name_for_dork = subject.name if subject else query

    # ─── Phase 1: Brave/DDG dork search (always runs) ──────
    dork_findings = _site_dork_search(
        "linkedin.com", name_for_dork, subject_id, icon="💼"
    )
    seen_urls = set()
    for f in dork_findings:
        url = f.get("source_url")
        if url:
            seen_urls.add(url)
    findings.extend(dork_findings)

    # ─── Phase 2: API enrichment (only if key + credits) ────
    if not api_key or not _has_credits("linkedin"):
        return findings

    def add_api_finding(name, url, detail=""):
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        findings.append(
            {
                "title": f"LinkedIn: {name}",
                "detail": detail or url or name,
                "source_url": url or "",
                "source_type": "linkedin",
                "icon": "💼",
                "verified": False,
                "screenshots": [{"url": None, "source_url": url}] if url else [],
            }
        )

    is_url = "linkedin.com" in query.lower() and query.startswith("http")
    is_username = (
        not is_url and "/" not in query and " " not in query and len(query) < 100
    )

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "linkedin-data-api.p.rapidapi.com",
            "Accept": "application/json",
        }

        def api_get(path, params):
            r = jittered_get(
                f"https://linkedin-data-api.p.rapidapi.com{path}",
                params=params,
                headers=headers,
                timeout=15,
            )
            if r.status_code in (401, 403, 429):
                return None
            if r.status_code != 200:
                return None
            try:
                return r.json()
            except Exception:
                return None

        def extract_profile(data):
            if isinstance(data, dict) and data.get("data"):
                data = data["data"]
            elif (
                isinstance(data, dict) and data.get("success") and not data.get("data")
            ):
                return None
            name = (
                data.get("full_name") or data.get("fullName") or data.get("name") or ""
            )
            url = (
                data.get("profile_url")
                or data.get("url")
                or data.get("linkedin_url")
                or ""
            )
            headline = data.get("headline") or ""
            location = data.get("location") or data.get("geo") or ""
            summary = data.get("summary") or data.get("about") or ""
            followers = data.get("follower_count") or data.get("followers") or ""
            connections = data.get("connection_count") or data.get("connections") or ""
            return name, url, headline, location, summary, followers, connections

        if is_url:
            data = api_get("/get-profile-data-by-url", {"url": query})
            if data:
                result = extract_profile(data)
                if result and result[0]:
                    _use_credit("linkedin")
                    name, url, headline, location, summary, followers, connections = (
                        result
                    )
                    detail_parts = []
                    if headline:
                        detail_parts.append(f"💼 {headline}")
                    if location:
                        detail_parts.append(f"📍 {location}")
                    if followers:
                        detail_parts.append(f"👥 {followers} followers")
                    if summary:
                        sp = summary[:120] + "…" if len(summary) > 120 else summary
                        detail_parts.append(f"📝 {sp}")
                    add_api_finding(name, url or query, " · ".join(detail_parts))
                    return findings

        if is_username:
            profile_url = f"https://www.linkedin.com/in/{query}/"
            data = api_get("/get-profile-data-by-url", {"url": profile_url})
            if data:
                result = extract_profile(data)
                if result and result[0]:
                    _use_credit("linkedin")
                    name, url, headline, location, summary, followers, connections = (
                        result
                    )
                    detail_parts = []
                    if headline:
                        detail_parts.append(f"💼 {headline}")
                    if location:
                        detail_parts.append(f"📍 {location}")
                    if followers:
                        detail_parts.append(f"👥 {followers} followers")
                    if summary:
                        sp = summary[:120] + "…" if len(summary) > 120 else summary
                        detail_parts.append(f"📝 {sp}")
                    add_api_finding(name, url or profile_url, " · ".join(detail_parts))
                    return findings

        # Name search
        data = api_get("/search-people", {"keyword": query})
        if data:
            _use_credit("linkedin")
            items = data.get("data") if isinstance(data, dict) else data
            if isinstance(items, dict):
                items = (
                    items.get("results")
                    or items.get("people")
                    or items.get("items")
                    or []
                )
            if isinstance(items, list):
                for item in items[:5]:
                    if not isinstance(item, dict):
                        continue
                    result = extract_profile(item)
                    if not result or not result[0]:
                        continue
                    name, url, headline, location, summary, followers, connections = (
                        result
                    )
                    detail_parts = []
                    if headline:
                        detail_parts.append(f"💼 {headline}")
                    if location:
                        detail_parts.append(f"📍 {location}")
                    detail = " · ".join(detail_parts)
                    add_api_finding(name, url, detail)

    except Exception as e:
        logger.warning("LinkedIn API check failed: %s", e)

    return findings


def _twitter_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")

    query = action.data_value if action.data_value else None
    subject = _first_subject(action)
    if not query:
        query = subject.name if subject else ""
    if not query:
        return findings

    subject_id = subject.id if subject else None
    name_for_dork = subject.name if subject else query

    # ─── Phase 1: Brave/DDG dork search (always runs) ──────
    # Twitter/X: search both domains
    dork_findings = _site_dork_search(
        "twitter.com", name_for_dork, subject_id, icon="🐦"
    )
    dork_findings += _site_dork_search("x.com", name_for_dork, subject_id, icon="🐦")
    seen_urls = set()
    deduped = []
    for f in dork_findings:
        url = f.get("source_url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(f)
    findings.extend(deduped)

    # ─── Phase 2: API enrichment (only if key + credits) ────
    if not api_key or not _has_credits("twitter"):
        return findings

    def add_api_finding(name, url, detail=""):
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        findings.append(
            {
                "title": f"Twitter: {name}",
                "detail": detail or url or name,
                "source_url": url or "",
                "source_type": "twitter",
                "icon": "🐦",
                "verified": False,
                "screenshots": [{"url": None, "source_url": url}] if url else [],
            }
        )

    is_username = "/" not in query and " " not in query and len(query) < 100

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "twitter-api45.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = jittered_get(
                "https://twitter-api45.p.rapidapi.com/screenname.php",
                params={"screenname": query},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                body = r.json()
                if body and body.get("name"):
                    _use_credit("twitter")
                    name = body.get("name", "")
                    screen_name = body.get("screen_name", query)
                    url = f"https://x.com/{screen_name}"
                    detail_parts = []
                    if body.get("description"):
                        bp = (
                            body["description"][:120] + "…"
                            if len(body["description"]) > 120
                            else body["description"]
                        )
                        detail_parts.append(f"📝 {bp}")
                    if body.get("location"):
                        detail_parts.append(f"📍 {body['location']}")
                    if body.get("followers_count") is not None:
                        detail_parts.append(f"👥 {body['followers_count']:,} followers")
                    if body.get("friends_count") is not None:
                        detail_parts.append(f"↗ {body['friends_count']:,} following")
                    if body.get("verified") or body.get("is_blue_verified"):
                        detail_parts.append("✅ Verified")
                    add_api_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code == 429:
                logger.warning("Twitter API rate limit")
                return findings
            elif r.status_code in (401, 403):
                logger.warning("Twitter API auth error")
                return findings

        # Search fallback
        r = jittered_get(
            "https://twitter-api45.p.rapidapi.com/search.php",
            params={"query": query, "type": "Popular"},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            _use_credit("twitter")
            body = r.json()
            tweets = body.get("tweets") if isinstance(body, dict) else body
            if isinstance(tweets, list):
                seen = set()
                for item in tweets[:10]:
                    user = item.get("user") if isinstance(item, dict) else {}
                    if not isinstance(user, dict):
                        continue
                    screen_name = user.get("screen_name") or ""
                    name = user.get("name") or screen_name
                    if not name or screen_name in seen:
                        continue
                    seen.add(screen_name)
                    url = f"https://x.com/{screen_name}" if screen_name else ""
                    bio = user.get("description") or ""
                    detail = (
                        f"📝 {bio[:120]}{'…' if len(bio) > 120 else ''}" if bio else ""
                    )
                    add_api_finding(name, url, detail)

    except Exception as e:
        logger.warning("Twitter API check failed: %s", e)

    return findings


# ─── Subdomain scan (crt.sh) ──────────────────────────────


def _subdomain_check(action):
    findings = []
    domain = action.data_value if action.data_value else None
    if not domain:
        subject = _first_subject(action)
        if subject and subject.email and "@" in subject.email:
            domain = subject.email.split("@")[1]
    if not domain:
        return findings

    try:
        from cms.constants import HEADERS
        import time

        url = f"https://crt.sh/?q=%25.{domain}&output=json"
        headers = dict(HEADERS)
        headers["Accept"] = "application/json"

        subs = set()

        # Try up to 2 times with longer timeout
        for attempt in range(2):
            try:
                resp = jittered_get(url, headers=headers, timeout=60)
                if resp.status_code in (502, 503, 504):
                    time.sleep(2)
                    continue
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        body_len = len(
                            getattr(resp, "text", "")
                            or getattr(resp, "content", b"")
                            or ""
                        )
                        raise ValueError(
                            f"crt.sh returned no valid JSON ({body_len} bytes)"
                        )
                    for entry in data:
                        raw = entry.get("name_value", "")
                        for name in raw.split("\n"):
                            name = name.strip().lower()
                            if name.startswith("*."):
                                name = name[2:]
                            if name.endswith(f".{domain}") or name == domain:
                                subs.add(name)
                    break
            except Exception:
                if attempt == 0:
                    time.sleep(3)
                    continue
                raise

        if not subs:
            # Fallback: try certspotter API (no Cloudflare)
            try:
                cs_url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names&after="
                cs_headers = {
                    "Accept": "application/json",
                    "User-Agent": HEADERS.get("User-Agent", "Mozilla/5.0"),
                }
                cs_resp = jittered_get(cs_url, headers=cs_headers, timeout=30)
                if cs_resp.status_code == 200:
                    try:
                        cs_data = cs_resp.json()
                    except Exception:
                        cs_data = []
                    for entry in cs_data:
                        for name in entry.get("dns_names", []):
                            name = name.strip().lower().lstrip("*.")
                            if name.endswith(f".{domain}") or name == domain:
                                subs.add(name)
            except Exception:
                pass

        sorted_subs = sorted(subs)[:50]
        if not sorted_subs:
            findings.append(
                {
                    "title": f"No subdomains found for {domain}",
                    "detail": "No certificate transparency logs with subdomains were found.",
                    "source_type": "subdomain",
                    "icon": "🌐",
                    "verified": False,
                }
            )
            return findings

        findings.append(
            {
                "title": f"{len(sorted_subs)} subdomains found for {domain}",
                "detail": "\n".join(sorted_subs[:30])
                + ("\n... and more" if len(sorted_subs) > 30 else ""),
                "source_url": f"https://crt.sh/?q=%25.{domain}",
                "source_type": "subdomain",
                "icon": "🌐",
                "verified": True,
                "screenshots": [
                    {"url": None, "source_url": f"https://crt.sh/?q=%25.{domain}"}
                ],
            }
        )
    except Exception as e:
        findings.append(
            {
                "title": f"Subdomein scan error: {e}",
                "detail": str(e),
                "source_type": "subdomain",
                "icon": "🌐",
                "verified": False,
            }
        )
    return findings


# ─── Register all actions ─────────────────────────────────
register_action(
    "email",
    "Email check",
    "📧",
    _email_check,
    "Searches the email address in public sources (HIBP, PGP keyservers, SpiderFoot) and checks whether it "
    "appears in data breaches, has a PGP key, is linked to social media, "
    "or leaves other online traces (web context).",
)
register_action(
    "phone",
    "Phone check",
    "📞",
    _phone_check,
    "Searches public sources for the phone number: links to social media, business registrations, and any signals from data breaches.",
)
register_action(
    "address",
    "Address research",
    "🏠",
    _address_check,
    "Searches the address in public sources (Kadaster, Overheid.io) to find resident history, property information, and related addresses.",
)
register_action(
    "social",
    "Social media scan",
    "🌐",
    _social_scan,
    "Scans multiple social media platforms based on name or username and collects public profiles, posts, and network connections.",
)
register_action(
    "facebook",
    "Facebook research",
    "📘",
    _facebook_check,
    "Searches for public Facebook profiles, pages, and posts by name. Returns profile photo, bio, and public interactions.",
)
register_action(
    "instagram",
    "Instagram research",
    "📸",
    _instagram_check,
    "Searches Instagram for public profiles and posts. Finds username, profile photo, biography, and recent posts.",
)
register_action(
    "tiktok",
    "TikTok research",
    "🎵",
    _tiktok_check,
    "Searches for public TikTok profiles and content. Returns username, avatar, bio, and video data. (50 credits)",
)
register_action(
    "linkedin",
    "LinkedIn research",
    "💼",
    _linkedin_check,
    "Searches for public LinkedIn profiles by name. Finds work experience, education, location, and network size. (50 credits)",
)
register_action(
    "twitter",
    "Twitter research",
    "🐦",
    _twitter_check,
    "Searches X/Twitter for public profiles and posts. Returns username, bio, followers, and recent tweets. (1000 credits)",
)
register_action(
    "kvk",
    "KvK research",
    "🏢",
    _kvk_check,
    "Consults the Chamber of Commerce (KvK) for company data: legal form, registered address, directors, and annual figures.",
)
register_action(
    "rdw",
    "Vehicle check (RDW)",
    "🚗",
    _rdw_check,
    "Retrieves vehicle information from the RDW database: license plate, make/type, APK history, technical specifications, and registration data.",
)
register_action(
    "vessel",
    "Vessel check",
    "🚢",
    _vessel_check,
    "Searches maritime data sources (VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis) "
    "by IMO number, MMSI, ENI, or vessel name. Finds position, technical specifications, flag, and year built.",
)
register_action(
    "osint",
    "OSINT Deep Search",
    "🌍",
    _osint_deep_search,
    "Performs in-depth open-source research via Brave Search and SpiderFoot. Searches the entire web for traces of the person or entity.",
)
register_action(
    "financial",
    "Financial research",
    "💰",
    _financial_check,
    "Searches public sources for financial data: business registers, insolvencies, UBO registers, and any negative financial signals.",
)
register_action(
    "subdomain",
    "Subdomain scan",
    "🌐",
    _subdomain_check,
    "Searches Certificate Transparency logs (crt.sh) for all subdomains of a domain. "
    "Finds internal hostnames, development environments, and hidden infrastructure.",
)

register_action(
    "manual_entry",
    "Manual entry",
    "📝",
    lambda action: [],
    "Manually created findings by the researcher.",
)
