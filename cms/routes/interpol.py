import logging
import re
import threading
import time

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Case, Finding, AuditLog
from ..validation import validate, CheckPolicieDataSchema, InterpolFindingSchema
from ..rate_limiting import rate_limit, STRICT_RATE_LIMIT
from ..auth import ensure_tenant_access
from ..api_key_auth import api_key_required
from ..feature_flags import tool_enabled
from curl_cffi.requests import Session
from cms.services.http_utils import jittered_get

from .response import api_error

logger = logging.getLogger(__name__)

_last_interpol_call = 0
_interpol_call_lock = threading.Lock()


def _check_interpol_rate_limit() -> float:
    global _last_interpol_call
    with _interpol_call_lock:
        elapsed = time.time() - _last_interpol_call
        if _last_interpol_call > 0 and elapsed < 60:
            return 60 - elapsed
        _last_interpol_call = time.time()
        return 0


def _interpol_headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (compatible; OSINT-CMS/2.0)",
        "Accept": "application/json",
    }


@cms_bp.route("/check-policie-data", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("interpol")
@login_required
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="interpol")
@validate(CheckPolicieDataSchema)
def check_policie_data() -> flask.Response:
    """Check subject against INTERPOL Red Notices + Yellow Notices + politie.nl."""
    data = request.validated_data

    subject_name = data.get("name", "").strip()
    subject_id = data.get("subject_id")

    if not subject_name:
        return api_error("Subject name is required", 400)

    name_parts = subject_name.lower().split()
    forename = name_parts[0] if len(name_parts) > 0 else ""
    surname = name_parts[-1] if len(name_parts) > 1 else ""

    results = {
        "subject_name": subject_name,
        "subject_id": subject_id,
        "missing_persons": [],
        "wanted_persons": [],
        "opsporingsberichten": [],
        "api_available": True,
        "source": "interpol",
        "error": None,
    }

    wait = _check_interpol_rate_limit()
    if wait > 0:
        return jsonify(
            {
                "subject_name": subject_name,
                "subject_id": subject_id,
                "missing_persons": [],
                "wanted_persons": [],
                "api_available": False,
                "source": "interpol",
                "error": f"Interpol API rate limit: wait {wait:.0f} seconds before next request",
                "retry_after": int(wait),
            }
        ), 429

    interpol_403 = False
    try:
        client = Session(
            impersonate="chrome124", headers=_interpol_headers(), timeout=15
        )

        try:
            red_params = {"resultPerPage": 10}
            if surname:
                red_params["name"] = surname
            if forename:
                red_params["forename"] = forename
            r = client.get(
                "https://ws-public.interpol.int/notices/v1/red", params=red_params
            )
            if r.status_code == 200:
                red_data = r.json()
                for notice in red_data.get("_embedded", {}).get("notices", []):
                    nid = notice["entity_id"].replace("/", "-")
                    detail = None
                    try:
                        dr = client.get(
                            f"https://ws-public.interpol.int/notices/v1/red/{nid}"
                        )
                        if dr.status_code == 200:
                            detail = dr.json()
                    except Exception as e:
                        logger.debug(
                            f"INTERPOL Red Notice detail fetch failed for {nid} ({type(e).__name__}): {e}"
                        )
                    charge = ""
                    issuing = ""
                    if detail and detail.get("arrest_warrants"):
                        aw = detail["arrest_warrants"][0]
                        charge = aw.get("charge", "")
                        issuing = aw.get("issuing_country_id", "")
                    results["wanted_persons"].append(
                        {
                            "name": f"{notice.get('forename', '')} {notice.get('name', '')}".strip(),
                            "forename": notice.get("forename", ""),
                            "surname": notice.get("name", ""),
                            "date_of_birth": notice.get("date_of_birth", ""),
                            "nationality": ", ".join(notice.get("nationalities", [])),
                            "charge": charge,
                            "issuing_country": issuing,
                            "url": notice.get("_links", {})
                            .get("self", {})
                            .get("href", ""),
                            "thumbnail": notice.get("_links", {})
                            .get("thumbnail", {})
                            .get("href", ""),
                            "type": "Red Notice (Wanted)",
                            "source": "INTERPOL",
                        }
                    )
            elif r.status_code == 403:
                interpol_403 = True
        except Exception as e:
            logger.warning(f"Interpol Red Notice lookup error: {e}")

        try:
            yellow_params = {"resultPerPage": 10}
            if surname:
                yellow_params["name"] = surname
            if forename:
                yellow_params["forename"] = forename
            r = client.get(
                "https://ws-public.interpol.int/notices/v1/yellow", params=yellow_params
            )
            if r.status_code == 200:
                yellow_data = r.json()
                for notice in yellow_data.get("_embedded", {}).get("notices", []):
                    nid = notice["entity_id"].replace("/", "-")
                    detail = None
                    try:
                        dr = client.get(
                            f"https://ws-public.interpol.int/notices/v1/yellow/{nid}"
                        )
                        if dr.status_code == 200:
                            detail = dr.json()
                    except Exception as e:
                        logger.debug(
                            f"INTERPOL Yellow Notice detail fetch failed for {nid} ({type(e).__name__}): {e}"
                        )
                    results["missing_persons"].append(
                        {
                            "name": f"{notice.get('forename', '')} {notice.get('name', '')}".strip(),
                            "forename": notice.get("forename", ""),
                            "surname": notice.get("name", ""),
                            "date_of_birth": notice.get("date_of_birth", ""),
                            "nationality": ", ".join(notice.get("nationalities", [])),
                            "date_missing": detail.get("date_of_event", "")
                            if detail
                            else "",
                            "place": detail.get("place", "") if detail else "",
                            "countries_likely_to_visit": ", ".join(
                                detail.get("countries_likely_to_be_visited", [])
                            )
                            if detail
                            else "",
                            "url": notice.get("_links", {})
                            .get("self", {})
                            .get("href", ""),
                            "thumbnail": notice.get("_links", {})
                            .get("thumbnail", {})
                            .get("href", ""),
                            "type": "Yellow Notice (Missing)",
                            "source": "INTERPOL",
                        }
                    )
            elif r.status_code == 403:
                interpol_403 = True
        except Exception as e:
            logger.warning(f"Interpol Yellow Notice lookup error: {e}")

        if (
            len(results["wanted_persons"]) == 0
            and len(results["missing_persons"]) == 0
            and len(name_parts) >= 1
        ):
            try:
                vermist_resp = jittered_get(
                    "https://www.politie.nl/vermist",
                    headers=_interpol_headers(),
                    timeout=10,
                )
                if vermist_resp.status_code == 200:
                    case_links = re.findall(
                        r'href="(/vermist/[^"]+)"', vermist_resp.text
                    )
                    for link in case_links[:20]:
                        try:
                            detail = jittered_get(
                                f"https://www.politie.nl{link}",
                                headers=_interpol_headers(),
                                timeout=10,
                            )
                            if detail.status_code == 200:
                                text_lower = detail.text.lower()
                                if any(part in text_lower for part in name_parts):
                                    title_match = re.search(
                                        r"<h1[^>]*>([^<]+)</h1>", detail.text
                                    )
                                    title = (
                                        title_match.group(1).strip()
                                        if title_match
                                        else "Unknown"
                                    )
                                    results["missing_persons"].append(
                                        {
                                            "name": title,
                                            "source": "politie.nl/vermist",
                                            "url": f"https://www.politie.nl{link}",
                                            "type": "Missing Person (Netherlands)",
                                            "description": "Matching name parts found on politie.nl",
                                        }
                                    )
                        except Exception as e:
                            logger.debug(
                                f"Politie.nl/vermist detail page fetch failed for {link} ({type(e).__name__}): {e}"
                            )
            except Exception as e:
                logger.warning(
                    f"Politie.nl/vermist main page scrape failed ({type(e).__name__}): {e}"
                )

        results["api_available"] = not interpol_403
        if (
            interpol_403
            and len(results["wanted_persons"]) == 0
            and len(results["missing_persons"]) == 0
        ):
            results["error"] = (
                "INTERPOL API is tijdelijk geblokkeerd (Akamai). Politie.nl check uitgevoerd als fallback."
            )
            results["source"] = "politie.nl (fallback)"

        try:
            from cms.politie_scraper import search_opsporingsberichten

            gezocht = search_opsporingsberichten(
                forename=forename, surname=surname, max_pages=2
            )
            results["opsporingsberichten"] = gezocht.get("matches", [])
        except Exception as e:
            logger.warning(f"Opsporingsberichten check error: {e}")

        return jsonify(results), 200
    except Exception:
        logger.exception("Interpol data check error")
        return jsonify({"error": "Failed to check data", "api_available": False}), 500


@cms_bp.route("/check-policie-data-status", methods=["GET"])
@login_required
def check_policie_api_status() -> flask.Response:
    """Check if INTERPOL API is available."""
    wait = _check_interpol_rate_limit()
    if wait > 0:
        return jsonify(
            {
                "available": False,
                "status_code": 429,
                "error": f"Rate limited, retry in {wait:.0f}s",
                "retry_after": int(wait),
            }
        ), 200
    try:
        r = jittered_get(
            "https://ws-public.interpol.int/notices/v1/red",
            params={"resultPerPage": 1},
            headers=_interpol_headers(),
            timeout=10,
        )
        return jsonify(
            {
                "available": r.status_code == 200,
                "status_code": r.status_code,
                "api_url": "https://ws-public.interpol.int/notices/v1/",
                "source": "INTERPOL",
            }
        ), 200
    except Exception:
        logger.exception("Interpol API status check error")
        return jsonify({"available": False, "error": "API check failed"}), 200


@cms_bp.route("/api/findings/from-interpol", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("interpol")
@login_required
@validate(InterpolFindingSchema)
def create_findings_from_interpol() -> flask.Response:
    """Save Interpol/politie check results as findings."""
    data = request.validated_data

    case_id = data.get("case_id")
    subject_id = data.get("subject_id")
    wanted = data.get("wanted_persons", [])
    missing = data.get("missing_persons", [])
    opsporingen = data.get("opsporingsberichten", [])

    if not case_id:
        return api_error("case_id is required", 400)
    if not wanted and not missing and not opsporingen:
        return api_error("No results to save", 400)

    case = db.session.get(Case, case_id)
    if not case:
        return api_error("Case not found", 404)
    ensure_tenant_access(case)

    created = []
    for p in wanted:
        content_parts = ["Type: Red Notice (Wanted)"]
        if p.get("date_of_birth"):
            content_parts.append(f"DOB: {p['date_of_birth']}")
        if p.get("nationality"):
            content_parts.append(f"Nationality: {p['nationality']}")
        if p.get("charge"):
            content_parts.append(f"Charge: {p['charge']}")
        if p.get("issuing_country"):
            content_parts.append(f"Issued by: {p['issuing_country']}")
        if p.get("url"):
            content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"INTERPOL Red Notice: {p.get('name', 'Unknown')}",
            content="\n".join(content_parts),
            source_url=p.get("url", ""),
            source_type="interpol",
            finding_type="identity",
            reliability_score=7,
            confidence_level="medium",
            tags=["interpol", "red_notice", "wanted"],
            created_by=current_user.id,
        )
        db.session.add(finding)
        created.append(finding)

    for p in missing:
        content_parts = [f"Type: {p.get('type', 'Missing Person')}"]
        if p.get("date_of_birth"):
            content_parts.append(f"DOB: {p['date_of_birth']}")
        if p.get("nationality"):
            content_parts.append(f"Nationality: {p['nationality']}")
        if p.get("date_missing"):
            content_parts.append(f"Missing since: {p['date_missing']}")
        if p.get("place"):
            content_parts.append(f"Place: {p['place']}")
        if p.get("countries_likely_to_visit"):
            content_parts.append(f"Likely locations: {p['countries_likely_to_visit']}")
        if p.get("source") and p["source"] != "INTERPOL":
            content_parts.append(f"Source: {p['source']}")
        if p.get("description"):
            content_parts.append(f"Info: {p['description']}")
        if p.get("url"):
            content_parts.append(f"URL: {p['url']}")

        tags = (
            ["interpol", "yellow_notice", "missing"]
            if p.get("source") == "INTERPOL"
            else ["interpol", "vermist", "missing"]
        )
        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"INTERPOL / Vermist: {p.get('name', 'Unknown')}",
            content="\n".join(content_parts),
            source_url=p.get("url", ""),
            source_type="interpol",
            finding_type="identity",
            reliability_score=7,
            confidence_level="medium",
            tags=tags,
            created_by=current_user.id,
        )
        db.session.add(finding)
        created.append(finding)

    for p in opsporingen:
        content_parts = ["Type: Opsporingsbericht (Politie.nl)"]
        if p.get("location"):
            content_parts.append(f"Locatie: {p['location']}")
        if p.get("date"):
            content_parts.append(f"Datum: {p['date']}")
        if p.get("url"):
            content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"Opsporingsbericht: {p.get('title', 'Unknown')}",
            content="\n".join(content_parts),
            source_url=p.get("url", ""),
            source_type="politie",
            finding_type="identity",
            reliability_score=6,
            confidence_level="medium",
            tags=["politie", "opsporingsbericht", "gezocht"],
            created_by=current_user.id,
        )
        db.session.add(finding)
        created.append(finding)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="finding",
        entity_id=None,
        ip_address=request.remote_addr,
        case_id=case_id,
        new_values={"count": len(created), "source": "interpol_check"},
        description=f"Added {len(created)} Interpol findings to case {case.case_number}",
    )
    db.session.commit()

    return jsonify(
        {
            "message": f"{len(created)} finding(s) saved",
            "count": len(created),
            "findings": [f.to_dict() for f in created],
        }
    ), 201
