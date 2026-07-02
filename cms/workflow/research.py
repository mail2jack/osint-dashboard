import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime

from cms.models import db
from .models import (
    WorkflowCase,
    WorkflowResearchAction,
    WorkflowFinding,
    WorkflowScreenshot,
    WorkflowActionFinding,
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
            pass


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
        action.result_summary = "Geannuleerd"
        links = WorkflowActionFinding.query.filter_by(action_id=action_id).all()
        if links:
            finding_ids = [l.finding_id for l in links]
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
            action.result_summary = "Geannuleerd"
            db.session.commit()
            return

        created = []
        for fd in findings_data:
            detail_text = fd.get("detail", "")
            finding = WorkflowFinding(
                id=str(uuid.uuid4()),
                case_id=action.case_id,
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

            created.append(finding)

        action.status = "completed"
        action.completed_at = datetime.now()
        action.result_summary = f"{len(created)} bevindingen"
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
                site_name = acct.get("name") or acct.get("site") or "onbekend"
                findings.append(
                    {
                        "title": f"Account gevonden op {site_name}",
                        "detail": f"E-mail {email} is geregistreerd op {site_name}. "
                        f"Status: {acct.get('status', 'onbekend')}",
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
                    "title": f"{len(unverified)} site(s) reageren maar account niet bevestigd",
                    "detail": f"Voor {email} reageerden {len(unverified)} site(s) met een 200-status, "
                    f"maar de pagina bevatte geen bevestiging van het account. "
                    f"Dit zijn waarschijnlijk valse positieven.",
                    "source_type": "email",
                    "icon": "⚠️",
                    "verified": False,
                }
            )
        if not confirmed and not unverified:
            findings.append(
                {
                    "title": "Geen accounts gevonden via e-mail search",
                    "detail": f"Voor {email} zijn geen geregistreerde accounts gevonden.",
                    "source_type": "email",
                    "icon": "📧",
                    "verified": False,
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"E-mail check mislukt: {e}",
                "detail": str(e),
                "source_type": "email",
                "icon": "📧",
                "verified": False,
            }
        )

    hibp_key = _get_api_key("hibp_api_key")
    if hibp_key:
        try:
            import requests

            r = requests.get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                headers={"hibp-api-key": hibp_key, "user-agent": "Iveras-Workflow"},
                timeout=15,
            )
            if r.status_code == 200:
                for breach in r.json():
                    findings.append(
                        {
                            "title": f"Datalek: {breach['Name']}",
                            "detail": f"Domain: {breach.get('Domain', '')}. "
                            f"Data: {', '.join(breach.get('DataClasses', []))}. "
                            f"Datum: {breach.get('BreachDate', '')}",
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
        from curl_cffi import requests as curl_requests

        pgp_url = f"https://keys.openpgp.org/vks/v1/by-email/{quote(email)}"
        pgp_resp = curl_requests.get(pgp_url, impersonate="chrome", timeout=10)
        if pgp_resp.status_code == 200:
            findings.append(
                {
                    "title": "PGP-sleutel gevonden",
                    "detail": (
                        f"Voor {email} is een PGP-public key gevonden op "
                        f"keys.openpgp.org. Dit bevestigt dat de eigenaar "
                        f"PGP-versleuteling gebruikt voor e-mailcommunicatie."
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
                        "title": f"Vermeld op: {res.get('title', 'onbekend')[:200]}",
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
    try:
        import phonenumbers

        parsed = phonenumbers.parse(phone, "NL")
        if phonenumbers.is_valid_number(parsed):
            findings.append(
                {
                    "title": f"Telefoonnummer {phone} is geldig",
                    "detail": f"Land: {phonenumbers.region_code_for_number(parsed)}. "
                    f"Type: {phonenumbers.number_type(parsed)}",
                    "source_type": "phone",
                    "icon": "📞",
                    "verified": False,
                }
            )
    except Exception:
        pass

    from cms.services.phone_service import (
        _whatsapp_check_internal,
        _telegram_check_internal,
    )

    wa = _whatsapp_check_internal(phone)
    if wa.get("exists"):
        findings.append(
            {
                "title": "WhatsApp account gevonden",
                "detail": "Nummer gevonden op WhatsApp",
                "source_type": "phone",
                "icon": "💬",
                "verified": False,
                "screenshots": [{"url": None, "source_url": wa.get("url")}],
            }
        )
    tg = _telegram_check_internal(phone)
    if tg.get("exists"):
        findings.append(
            {
                "title": "Telegram account gevonden",
                "detail": "Nummer gevonden op Telegram",
                "source_type": "phone",
                "icon": "✈️",
                "verified": False,
            }
        )
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
                "title": "Geen adresgegevens opgegeven",
                "detail": "Voer straat, huisnummer, postcode en plaats in bij de betrokkene.",
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
            }
        )
        return findings
    try:
        import requests

        r = requests.get(
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
                details.append(f"Adres: {doc['straatnaam']} {hn_display}")
            if doc.get("postcode"):
                details.append(f"Postcode: {doc['postcode']}")
            if doc.get("woonplaatsnaam"):
                details.append(f"Plaats: {doc['woonplaatsnaam']}")
            if doc.get("buurtnaam"):
                details.append(f"Buurt: {doc['buurtnaam']}")
            if doc.get("wijknaam"):
                details.append(f"Wijk: {doc['wijknaam']}")
            if doc.get("gemeentenaam"):
                details.append(f"Gemeente: {doc['gemeentenaam']}")
            if doc.get("provincienaam"):
                details.append(f"Provincie: {doc['provincienaam']}")
            if doc.get("gekoppeld_perceel"):
                percelen = "; ".join(doc["gekoppeld_perceel"])
                details.append(f"Kadastraal perceel: {percelen}")
            if doc.get("gekoppeld_appartement"):
                apps = "; ".join(doc["gekoppeld_appartement"])
                details.append(f"Appartementsrecht: {apps}")
            if doc.get("openbareruimtetype"):
                details.append(f"Openbare ruimte type: {doc['openbareruimtetype']}")

            findings.append(
                {
                    "title": f"Adres: {doc.get('weergavenaam', address_query)}",
                    "detail": "\n".join(details)
                    if details
                    else "BAG registratie gevonden.",
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
                "title": f"Adres check mislukt: {e}",
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
                "detail": "Kadaster lookup gaf geen resultaten. Mogelijk niet gevonden in BAG.",
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
            }
        )
    return findings


def _social_scan(action):
    findings = []
    social_accounts = None
    if action.data_value:
        try:
            social_accounts = json.loads(action.data_value)
        except (json.JSONDecodeError, TypeError):
            pass
    if not social_accounts:
        all_accounts = []
        for subject in action.case.subjects or []:
            accounts = getattr(subject, "social_accounts", None) or []
            if accounts:
                all_accounts.extend(accounts)
            elif subject.name:
                all_accounts.append(subject.name)
        if all_accounts:
            social_accounts = all_accounts

    from cms.username_search import search_username, search_username_maigret

    if social_accounts:
        for acct in social_accounts:
            username = None
            label = None
            if isinstance(acct, dict):
                url = acct.get("url", "")
                username = url.rstrip("/").split("/")[-1] if url else None
                label = acct.get("platform", username or "onbekend")
            elif isinstance(acct, str):
                username = acct.strip()
                if username.startswith("@"):
                    username = username[1:]
                label = username
            if username:
                seen_sites = set()

                try:
                    maigret_result = search_username_maigret(username)
                    if maigret_result.get("found_count", 0) > 0:
                        for f in maigret_result.get("findings", []):
                            site = f.get("site") or f.get("platform", "")
                            if f.get("exists") == True and site not in seen_sites:
                                seen_sites.add(site)
                                findings.append(
                                    {
                                        "title": f"{label}: profiel actief ({site})",
                                        "detail": f"Gevonden via Maigret. URL: {f.get('url', '')}",
                                        "source_url": f.get("url"),
                                        "source_type": "social",
                                        "icon": "🌐",
                                        "verified": False,
                                        "screenshots": [
                                            {"url": None, "source_url": f.get("url")}
                                        ],
                                    }
                                )
                except Exception as e:
                    logger.warning("Maigret search for %s failed: %s", username, e)

                try:
                    sherlock_result = search_username(username)
                    if sherlock_result.get("found_count", 0) > 0:
                        for f in sherlock_result.get("findings", []):
                            site = f.get("platform") or f.get("site", "")
                            if f.get("exists") == True and site not in seen_sites:
                                seen_sites.add(site)
                                findings.append(
                                    {
                                        "title": f"{label}: profiel actief ({site})",
                                        "detail": f"Gevonden via Sherlock. URL: {f.get('url', '')}",
                                        "source_url": f.get("url"),
                                        "source_type": "social",
                                        "icon": "🌐",
                                        "verified": False,
                                        "screenshots": [
                                            {"url": None, "source_url": f.get("url")}
                                        ],
                                    }
                                )
                except Exception as e:
                    logger.warning("Sherlock search for %s failed: %s", username, e)

    if not findings:
        if social_accounts:
            for acct in social_accounts:
                if isinstance(acct, dict):
                    findings.append(
                        {
                            "title": f"Social media: {acct.get('platform', 'onbekend')}",
                            "detail": f"URL: {acct.get('url', '')}",
                            "source_url": acct.get("url"),
                            "source_type": "social",
                            "icon": "🌐",
                            "verified": False,
                        }
                    )
                elif isinstance(acct, str):
                    findings.append(
                        {
                            "title": f"Social media: {acct}",
                            "detail": f"Gebruikersnaam: {acct}",
                            "source_type": "social",
                            "icon": "🌐",
                            "verified": False,
                        }
                    )
        else:
            findings.append(
                {
                    "title": "Geen social media accounts om te scannen",
                    "detail": "Er zijn geen social media accounts opgegeven voor dit subject.",
                    "source_type": "social",
                    "icon": "🌐",
                    "verified": False,
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
            import requests

            r = requests.get(
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
                            "title": f"KvK: {item.get('naam', 'onbekend')}",
                            "detail": f"KvK: {item.get('kvkNummer', '')}. "
                            f"Adres: {item.get('straat', '')} {item.get('huisnummer', '')}, "
                            f"{item.get('postcode', '')} {item.get('plaats', '')}. "
                            f"Rechtsvorm: {item.get('rechtsvorm', '')}",
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
        from curl_cffi import requests as curl_requests
        import html as _html

        session = curl_requests.Session(impersonate="chrome")
        r = session.get(
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
                    "title": "KvK lookup openkvk.nl mislukt",
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
                    "detail": "Geen resultaten gevonden op openkvk.nl.",
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
                    "detail": "Geen bedrijven gevonden.",
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

            naam = company.get("naam", "onbekend")
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
                extra = f"Handelsnamen: {', '.join(handelsnamen[:5])}."

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
                "title": "KvK lookup via openkvk.nl mislukt",
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
                "title": "Geen kenteken opgegeven voor RDW check",
                "detail": "Voeg een kenteken toe aan het subject via het kentekenveld.",
                "source_type": "rdw",
                "icon": "🚗",
                "verified": False,
            }
        )
        return findings
    try:
        import requests

        kenteken = ipc.replace("-", "").replace(" ", "").upper()
        r = requests.get(
            "https://opendata.rdw.nl/resource/m9d7-ebf2.json",
            params={"kenteken": kenteken},
            timeout=15,
        )
        if r.status_code == 200 and r.json():
            data = dict(r.json()[0])
            _fmt = lambda v: (
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
                detail_parts.append(f"Kenteken: {data['kenteken']}")
            if data.get("eerste_kleur"):
                detail_parts.append(f"Kleur: {data['eerste_kleur']}")
            if data.get("vermogen_massario"):
                detail_parts.append(f"{data['vermogen_massario']} kW")
            if data.get("brandstof_omschrijving"):
                detail_parts.append(data["brandstof_omschrijving"])
            if data.get("vervaldatum_apk"):
                detail_parts.append(f"APK: {_fmt(data['vervaldatum_apk'])}")
            findings.append(
                {
                    "title": f"🚗 {data.get('merk', 'onbekend')} {data.get('handelsbenaming', '')}",
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
                    "title": f"Geen RDW gegevens voor kenteken {kenteken}",
                    "detail": "Voertuig niet gevonden in RDW-registratie.",
                    "source_type": "rdw",
                    "icon": "🚗",
                    "verified": False,
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"RDW check mislukt: {e}",
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
                    pass
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
                "title": "Geen vaartuiggegevens opgegeven",
                "detail": "Voer een IMO, MMSI, ENI-nummer of scheepsnaam in, "
                "of koppel eerst een vaartuig-subject aan dit onderzoek.",
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
                parts.append(f"Naam: {result['name']}")
            if result.get("imo"):
                parts.append(f"IMO: {result['imo']}")
            if result.get("mmsi"):
                parts.append(f"MMSI: {result['mmsi']}")
            if result.get("eni"):
                parts.append(f"ENI: {result['eni']}")
            if result.get("flag"):
                parts.append(f"Vlag: {result['flag']}")
            if result.get("ship_type"):
                parts.append(f"Type: {result['ship_type']}")
            if result.get("length"):
                parts.append(f"Lengte: {result['length']} m")
            if result.get("beam"):
                parts.append(f"Breedte: {result['beam']} m")
            if result.get("year_built"):
                parts.append(f"Bouwjaar: {result['year_built']}")
            if result.get("callsign"):
                parts.append(f"Roepnaam: {result['callsign']}")
            if result.get("destination"):
                parts.append(f"Bestemming: {result['destination']}")
            if result.get("builder"):
                parts.append(f"Bouwer: {result['builder']}")
            if result.get("position"):
                pos = result["position"]
                lat, lon = pos.get("lat", "?"), pos.get("lon", "?")
                map_url = (
                    f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12"
                )
                parts.append(
                    f"Positie: {lat}, {lon} (https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12)"
                )
            if result.get("position_text"):
                from urllib.parse import quote

                map_q = quote(result["position_text"])
                parts.append(
                    f"Positie: {result['position_text']} (https://www.google.com/maps?q={map_q})"
                )
            if result.get("speed"):
                parts.append(f"Snelheid: {result['speed']} knopen")
            if result.get("course"):
                parts.append(f"Koers: {result['course']}°")
            if result.get("navigation_status"):
                parts.append(f"Status: {result['navigation_status']}")
            if result.get("draught"):
                parts.append(f"Diepgang: {result['draught']} m")
            if result.get("eta"):
                parts.append(f"ETA: {result['eta']}")

            sources = result.get("sources", [])
            if sources:
                parts.append(f"\nBronnen: {', '.join(sources)}")

            findings.append(
                {
                    "title": f"🚢 {result.get('name', 'Onbekend schip')}",
                    "detail": " · ".join(parts) if parts else "Gegevens gevonden",
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
                    "title": "Geen vaartuiggegevens gevonden",
                    "detail": "Geen maritieme bronnen hebben gegevens opgeleverd voor de opgegeven identifiers.",
                    "source_type": "vessel",
                    "icon": "🚢",
                    "verified": False,
                }
            )
    except Exception as e:
        logger.exception("Vessel check failed")
        findings.append(
            {
                "title": f"Vaartuig check mislukt: {e}",
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
    if not name:
        subject = _first_subject(action)
        name = getattr(subject, "name", None) if subject else None
    if not name:
        return findings
    brave_key = _get_api_key("brave_api_key")
    from cms.services.search_service import brave_search

    try:
        results = brave_search(name, api_key=brave_key)
        for res in results[:10]:
            findings.append(
                {
                    "title": f"OSINT: {res.get('title', 'onbekend')[:200]}",
                    "detail": res.get("description", "")[:300],
                    "source_url": res.get("url"),
                    "source_type": "osint",
                    "icon": "🌍",
                    "verified": False,
                    "screenshots": [{"url": None, "source_url": res.get("url")}],
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"OSINT deep search error: {e}",
                "detail": str(e),
                "source_type": "osint",
                "icon": "🌍",
                "verified": False,
            }
        )
    if not findings:
        try:
            from cms.services.search_service import person_dorks_search

            dork_results = person_dorks_search(name)
            for link in dork_results.get("search_links", [])[:10]:
                findings.append(
                    {
                        "title": f"OSINT: {link.get('title', 'resultaat')[:200]}",
                        "detail": link.get("snippet", "")[:300],
                        "source_url": link.get("url"),
                        "source_type": "osint",
                        "icon": "🌍",
                        "verified": False,
                        "screenshots": [{"url": None, "source_url": link.get("url")}],
                    }
                )
        except Exception as e:
            logger.warning("Dork search failed: %s", e)
    return findings


def _financial_check(action):
    """Placeholder — financieel onderzoek is handmatig in deze fase."""
    return [
        {
            "title": "Financieel onderzoek — handmatige actie",
            "detail": "Financieel onderzoek vereist handmatige aanvraag bij banken/toezichthouders. "
            "Deze actie dient als herinnering om rekeningafschriften en transactieoverzichten "
            "op te vragen bij de relevante instanties.",
            "source_type": "financial",
            "icon": "💰",
            "verified": False,
        }
    ]


def _facebook_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")
    if not api_key:
        findings.append(
            {
                "title": "Facebook check niet beschikbaar",
                "detail": "Geen RapidAPI key geconfigureerd. "
                "Voeg deze toe in Settings → API Keys (rapidapi_username_key). "
                "Key verkrijgbaar via RapidAPI: facebook-scraper3",
                "source_type": "facebook",
                "icon": "📘",
                "verified": False,
            }
        )
        return findings

    query = action.data_value if action.data_value else None
    if not query:
        subject = _first_subject(action)
        query = subject.name if subject else ""
    if not query:
        return findings

    seen_urls = set()

    def add_finding(name, url, detail=""):
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

    is_url = query.startswith("http://") or query.startswith("https://")
    is_username = (
        not is_url and "/" not in query and " " not in query and len(query) < 100
    )

    # ─── PullAPI (primary for URL/username) ────────────────
    if is_url or is_username:
        username_param = query
        if is_url:
            m = re.search(r"facebook\.com/(?:profile\.php\?id=)?([^/?&#]+)", query)
            if m:
                username_param = m.group(1)
        try:
            from curl_cffi import requests as curl_requests

            session = curl_requests.Session(impersonate="chrome")
            r = session.get(
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
                        detail_parts.append(f"👥 {data['follower_count']:,} volgers")
                    if data.get("bio"):
                        bp = (
                            data["bio"][:120] + "…"
                            if len(data["bio"]) > 120
                            else data["bio"]
                        )
                        detail_parts.append(f"📝 {bp}")
                    if data.get("is_verified"):
                        detail_parts.append("✅ Geverifieerd")
                    add_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code in (401, 403):
                add_finding(
                    "PullAPI authenticatiefout",
                    "",
                    "PullAPI key heeft geen toegang (abonneer op facebook-scraper-api9 op RapidAPI). Valt terug op Scraper3.",
                )
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
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "facebook-scraper3.p.rapidapi.com",
            "Accept": "application/json",
        }

        def api_get(path, params):
            r = session.get(
                f"https://facebook-scraper3.p.rapidapi.com{path}",
                params=params,
                headers=headers,
                timeout=15,
            )
            if r.status_code == 429:
                add_finding(
                    "Rate limit bereikt",
                    "",
                    "Facebook Scraper API rate limit overschreden (free plan: 100/mnd).",
                )
                return None
            if r.status_code in (401, 403):
                add_finding(
                    "Authenticatiefout",
                    "",
                    "Facebook Scraper API key is ongeldig of verlopen.",
                )
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
                add_finding(name, url, detail)

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
                    add_finding(name or query, url, " · ".join(detail_parts))
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
                add_finding(name or query, fb_url, " · ".join(detail_parts))
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

        if not findings:
            add_finding(
                "Geen Facebook resultaten gevonden",
                "",
                f"Geen profielen of pagina's gevonden voor '{query}' via Facebook Scraper API.",
            )

    except Exception as e:
        add_finding("Facebook check mislukt", "", str(e))

    return findings


def _tiktok_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")
    if not api_key:
        findings.append(
            {
                "title": "TikTok check niet beschikbaar",
                "detail": "Geen RapidAPI key geconfigureerd. "
                "Voeg deze toe in Settings → API Keys (rapidapi_username_key). "
                "Key verkrijgbaar via RapidAPI: scraptik",
                "source_type": "tiktok",
                "icon": "🎵",
                "verified": False,
            }
        )
        return findings

    if not _has_credits("tiktok"):
        rem = get_remaining_credits("tiktok")
        findings.append(
            {
                "title": "TikTok credits opgebruikt",
                "detail": f"Deze maand nog {rem} van de {CREDIT_LIMITS['tiktok']} credits over. "
                "Volgende maand worden ze gereset.",
                "source_type": "tiktok",
                "icon": "🎵",
                "verified": False,
            }
        )
        return findings

    query = action.data_value if action.data_value else None
    if not query:
        subject = _first_subject(action)
        query = subject.name if subject else ""
    if not query:
        return findings

    seen_urls = set()

    def add_finding(name, url, detail=""):
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
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "scraptik.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = session.get(
                "https://scraptik.p.rapidapi.com/get-user",
                params={"username": query},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("data") if isinstance(body, dict) else body
                if data and data.get("nickname"):
                    _use_credit("tiktok")
                    name = data.get("nickname", "") or data.get("uniqueId", "") or query
                    url = f"https://www.tiktok.com/@{data.get('uniqueId', query)}"
                    detail_parts = []
                    if data.get("signature"):
                        bp = (
                            data["signature"][:120] + "…"
                            if len(data["signature"]) > 120
                            else data["signature"]
                        )
                        detail_parts.append(f"📝 {bp}")
                    if data.get("followerCount") is not None:
                        detail_parts.append(f"👥 {data['followerCount']:,} volgers")
                    if data.get("followingCount") is not None:
                        detail_parts.append(f"↗ {data['followingCount']:,} volgend")
                    if data.get("verified"):
                        detail_parts.append("✅ Geverifieerd")
                    add_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code == 429:
                add_finding(
                    "TikTok rate limit",
                    "",
                    "ScrapTik rate limit overschreden (gratis: 50/maand).",
                )
                return findings
            elif r.status_code in (401, 403):
                add_finding(
                    "TikTok authenticatiefout", "", "ScrapTik API key is ongeldig."
                )

        # Name search fallback
        r = session.get(
            "https://scraptik.p.rapidapi.com/search-users",
            params={"keyword": query, "count": "5"},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            _use_credit("tiktok")
            body = r.json()
            items = body.get("data") if isinstance(body, dict) else body
            if isinstance(items, dict):
                items = items.get("users") or items.get("results") or [items]
            if isinstance(items, list):
                for item in items[:5]:
                    if not isinstance(item, dict):
                        continue
                    name = (
                        item.get("nickname")
                        or item.get("uniqueId")
                        or item.get("username")
                        or ""
                    )
                    uid = item.get("uniqueId") or item.get("username") or ""
                    if not name:
                        continue
                    url = f"https://www.tiktok.com/@{uid}" if uid else ""
                    bio = item.get("signature") or ""
                    detail = (
                        f"📝 {bio[:120]}{'…' if len(bio) > 120 else ''}" if bio else ""
                    )
                    add_finding(name, url, detail)

        if not findings:
            add_finding(
                "Geen TikTok resultaten gevonden",
                "",
                f"Geen gebruikers gevonden voor '{query}' via ScrapTik.",
            )

    except Exception as e:
        add_finding("TikTok check mislukt", "", str(e))

    return findings


def _instagram_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")
    if not api_key:
        findings.append(
            {
                "title": "Instagram check niet beschikbaar",
                "detail": "Geen RapidAPI key geconfigureerd. "
                "Voeg deze toe in Settings → API Keys (rapidapi_username_key). "
                "Key verkrijgbaar via RapidAPI: instagram-scraper-api14",
                "source_type": "instagram",
                "icon": "📸",
                "verified": False,
            }
        )
        return findings

    if not _has_credits("instagram"):
        rem = get_remaining_credits("instagram")
        findings.append(
            {
                "title": "Instagram credits opgebruikt",
                "detail": f"Deze maand nog {rem} van de {CREDIT_LIMITS['instagram']} credits over. "
                "Volgende maand worden ze gereset.",
                "source_type": "instagram",
                "icon": "📸",
                "verified": False,
            }
        )
        return findings

    query = action.data_value if action.data_value else None
    if not query:
        subject = _first_subject(action)
        query = subject.name if subject else ""
    if not query:
        return findings

    seen_urls = set()

    def add_finding(name, url, detail=""):
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
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "instagram-scraper-api14.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = session.get(
                "https://instagram-scraper-api14.p.rapidapi.com/instagram/profile",
                params={"username": query},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("data") if isinstance(body, dict) else body
                if data and data.get("full_name"):
                    name = data.get("full_name", "")
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
                        detail_parts.append(f"👥 {data['follower_count']:,} volgers")
                    if data.get("following_count") is not None:
                        detail_parts.append(f"↗ {data['following_count']:,} volgend")
                    if data.get("is_verified"):
                        detail_parts.append("✅ Geverifieerd")
                    if data.get("business_category"):
                        detail_parts.append(f"🏢 {data['business_category']}")
                    _use_credit("instagram")
                    add_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code == 429:
                add_finding(
                    "Instagram rate limit",
                    "",
                    "PullAPI Instagram rate limit overschreden (gratis: 10/maand).",
                )
                return findings
            elif r.status_code in (401, 403):
                add_finding(
                    "Instagram authenticatiefout",
                    "",
                    "PullAPI Instagram key is ongeldig.",
                )

        # Name search fallback
        r = session.get(
            "https://instagram-scraper-api14.p.rapidapi.com/instagram/search",
            params={"query": query},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            _use_credit("instagram")
            body = r.json()
            data = body.get("data") if isinstance(body, dict) else body
            if isinstance(data, dict):
                users = data.get("users") or data.get("results") or []
            elif isinstance(data, list):
                users = data
            else:
                users = []
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
                add_finding(name, url, detail)

        if not findings:
            add_finding(
                "Geen Instagram resultaten gevonden",
                "",
                f"Geen profielen gevonden voor '{query}' via PullAPI Instagram.",
            )

    except Exception as e:
        add_finding("Instagram check mislukt", "", str(e))

    return findings


def _linkedin_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")
    if not api_key:
        findings.append(
            {
                "title": "LinkedIn check niet beschikbaar",
                "detail": "Geen RapidAPI key geconfigureerd. "
                "Voeg deze toe in Settings → API Keys (rapidapi_username_key). "
                "Key verkrijgbaar via RapidAPI: linkedin-data-api",
                "source_type": "linkedin",
                "icon": "💼",
                "verified": False,
            }
        )
        return findings

    if not _has_credits("linkedin"):
        rem = get_remaining_credits("linkedin")
        findings.append(
            {
                "title": "LinkedIn credits opgebruikt",
                "detail": f"Deze maand nog {rem} van de {CREDIT_LIMITS['linkedin']} credits over. "
                "Volgende maand worden ze gereset.",
                "source_type": "linkedin",
                "icon": "💼",
                "verified": False,
            }
        )
        return findings

    query = action.data_value if action.data_value else None
    if not query:
        subject = _first_subject(action)
        query = subject.name if subject else ""
    if not query:
        return findings

    seen_urls = set()

    def add_finding(name, url, detail=""):
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
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "linkedin-data-api.p.rapidapi.com",
            "Accept": "application/json",
        }

        def api_get(path, params):
            r = session.get(
                f"https://linkedin-data-api.p.rapidapi.com{path}",
                params=params,
                headers=headers,
                timeout=15,
            )
            if r.status_code == 429:
                add_finding(
                    "LinkedIn rate limit",
                    "",
                    "LinkedIn Data API rate limit overschreden.",
                )
                return None
            if r.status_code in (401, 403):
                add_finding(
                    "LinkedIn authenticatiefout",
                    "",
                    "LinkedIn Data API key is ongeldig.",
                )
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
                        detail_parts.append(f"👥 {followers} volgers")
                    if summary:
                        sp = summary[:120] + "…" if len(summary) > 120 else summary
                        detail_parts.append(f"📝 {sp}")
                    add_finding(name, url or query, " · ".join(detail_parts))
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
                        detail_parts.append(f"👥 {followers} volgers")
                    if summary:
                        sp = summary[:120] + "…" if len(summary) > 120 else summary
                        detail_parts.append(f"📝 {sp}")
                    add_finding(name, url or profile_url, " · ".join(detail_parts))
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
                    add_finding(name, url, detail)

        if not findings:
            add_finding(
                "Geen LinkedIn resultaten gevonden",
                "",
                f"Geen profielen gevonden voor '{query}' via LinkedIn Data API.",
            )

    except Exception as e:
        add_finding("LinkedIn check mislukt", "", str(e))

    return findings


def _twitter_check(action):
    findings = []
    api_key = _get_api_key("rapidapi_username_key")
    if not api_key:
        findings.append(
            {
                "title": "Twitter check niet beschikbaar",
                "detail": "Geen RapidAPI key geconfigureerd. "
                "Voeg deze toe in Settings → API Keys (rapidapi_username_key). "
                "Key verkrijgbaar via RapidAPI: twitter-api45",
                "source_type": "twitter",
                "icon": "🐦",
                "verified": False,
            }
        )
        return findings

    if not _has_credits("twitter"):
        rem = get_remaining_credits("twitter")
        findings.append(
            {
                "title": "Twitter credits opgebruikt",
                "detail": f"Deze maand nog {rem} van de {CREDIT_LIMITS['twitter']} credits over. "
                "Volgende maand worden ze gereset.",
                "source_type": "twitter",
                "icon": "🐦",
                "verified": False,
            }
        )
        return findings

    query = action.data_value if action.data_value else None
    if not query:
        subject = _first_subject(action)
        query = subject.name if subject else ""
    if not query:
        return findings

    seen_urls = set()

    def add_finding(name, url, detail=""):
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
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "twitter-api45.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = session.get(
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
                        detail_parts.append(f"👥 {body['followers_count']:,} volgers")
                    if body.get("friends_count") is not None:
                        detail_parts.append(f"↗ {body['friends_count']:,} volgend")
                    if body.get("verified") or body.get("is_blue_verified"):
                        detail_parts.append("✅ Geverifieerd")
                    add_finding(name, url, " · ".join(detail_parts))
                    return findings
            elif r.status_code == 429:
                add_finding(
                    "Twitter rate limit", "", "Twitter API rate limit overschreden."
                )
                return findings
            elif r.status_code in (401, 403):
                add_finding(
                    "Twitter authenticatiefout", "", "Twitter API key is ongeldig."
                )

        # Search fallback
        r = session.get(
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
                    add_finding(name, url, detail)

        if not findings:
            add_finding(
                "Geen Twitter resultaten gevonden",
                "",
                f"Geen gebruikers gevonden voor '{query}' via Twitter API.",
            )

    except Exception as e:
        add_finding("Twitter check mislukt", "", str(e))

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
        from curl_cffi import requests as curl_requests
        from cms.constants import HEADERS
        import time

        url = f"https://crt.sh/?q=%25.{domain}&output=json"
        headers = dict(HEADERS)
        headers["Accept"] = "application/json"

        subs = set()

        # Try up to 2 times with longer timeout
        for attempt in range(2):
            try:
                resp = curl_requests.get(url, headers=headers, timeout=60)
                if resp.status_code in (502, 503, 504):
                    time.sleep(2)
                    continue
                if resp.status_code == 200:
                    for entry in resp.json():
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
                cs_resp = curl_requests.get(cs_url, headers=cs_headers, timeout=30)
                if cs_resp.status_code == 200:
                    for entry in cs_resp.json():
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
                    "title": f"Geen subdomeinen gevonden voor {domain}",
                    "detail": "Er zijn geen certificaat-transparantie logs gevonden met subdomeinen.",
                    "source_type": "subdomain",
                    "icon": "🌐",
                    "verified": False,
                }
            )
            return findings

        findings.append(
            {
                "title": f"{len(sorted_subs)} subdomeinen gevonden voor {domain}",
                "detail": "\n".join(sorted_subs[:30])
                + ("\n… en meer" if len(sorted_subs) > 30 else ""),
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
    "E-mail check",
    "📧",
    _email_check,
    "Zoekt het e-mailadres op in openbare bronnen (HIBP, PGP keyservers, SpiderFoot) en controleert of het "
    "voorkomt in datalekken, een PGP-sleutel heeft, gekoppeld is aan sociale media, "
    "of andere online sporen (webcontext) achterlaat.",
)
register_action(
    "phone",
    "Telefoon check",
    "📞",
    _phone_check,
    "Doorzoekt openbare bronnen naar het telefoonnummer: koppelingen met sociale media, bedrijfsregistraties, en eventuele signaleringen uit datalekken.",
)
register_action(
    "address",
    "Adres onderzoek",
    "🏠",
    _address_check,
    "Zoekt het adres op in openbare bronnen (Kadaster, Overheid.io) om bewonershistorie, eigendomsinformatie en gerelateerde adressen te vinden.",
)
register_action(
    "social",
    "Social media scan",
    "🌐",
    _social_scan,
    "Scant meerdere sociale media platforms op basis van naam of gebruikersnaam en verzamelt openbare profielen, berichten en netwerkconnecties.",
)
register_action(
    "facebook",
    "Facebook onderzoek",
    "📘",
    _facebook_check,
    "Zoekt naar openbare Facebook profielen, pagina's en berichten op basis van naam. Levert profielfoto, bio en openbare interacties.",
)
register_action(
    "instagram",
    "Instagram onderzoek",
    "📸",
    _instagram_check,
    "Doorzoekt Instagram op openbare profielen en berichten. Vindt gebruikersnaam, profielfoto, biografie en recente posts.",
)
register_action(
    "tiktok",
    "TikTok onderzoek",
    "🎵",
    _tiktok_check,
    "Zoekt naar openbare TikTok profielen en content. Levert gebruikersnaam, avatar, bio en videogegevens. (50 credits)",
)
register_action(
    "linkedin",
    "LinkedIn onderzoek",
    "💼",
    _linkedin_check,
    "Zoekt naar openbare LinkedIn profielen op naam. Vindt werkervaring, opleiding, locatie en netwerkgrootte. (50 credits)",
)
register_action(
    "twitter",
    "Twitter onderzoek",
    "🐦",
    _twitter_check,
    "Doorzoekt X/Twitter naar openbare profielen en berichten. Levert gebruikersnaam, bio, volgers en recente tweets. (1000 credits)",
)
register_action(
    "kvk",
    "KvK onderzoek",
    "🏢",
    _kvk_check,
    "Raadpleegt de Kamer van Koophandel (KvK) voor bedrijfsgegevens: rechtsvorm, vestigingsadres, bestuurders en jaarcijfers.",
)
register_action(
    "rdw",
    "Voertuig check (RDW)",
    "🚗",
    _rdw_check,
    "Haalt voertuiginformatie op uit de RDW-databank: kenteken, merk/type, APK-historie, technische specificaties en tenaamstellingsgegevens.",
)
register_action(
    "vessel",
    "Vaartuig check",
    "🚢",
    _vessel_check,
    "Doorzoekt maritieme databronnen (VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis) "
    "op IMO-nummer, MMSI, ENI of scheepsnaam. Vindt positie, technische specificaties, vlag en bouwjaar.",
)
register_action(
    "osint",
    "OSINT Deep Search",
    "🌍",
    _osint_deep_search,
    "Voert een diepgaand open-source onderzoek uit via Brave Search en SpiderFoot. Doorzoekt het hele web op sporen van de persoon of entiteit.",
)
register_action(
    "financial",
    "Financieel onderzoek",
    "💰",
    _financial_check,
    "Doorzoekt openbare bronnen op financiële gegevens: bedrijfsregisters, insolventies, UBO-registers, en eventuele negatieve financiële signaleringen.",
)
register_action(
    "subdomain",
    "Subdomein scan",
    "🌐",
    _subdomain_check,
    "Doorzoekt Certificate Transparency logs (crt.sh) op alle subdomeinen van een domein. "
    "Vindt interne hostnames, ontwikkelomgevingen en verborgen infrastructuur.",
)
