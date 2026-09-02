import logging
import asyncio
import re
from cms.services.http_utils import jittered_get
from curl_cffi.requests import RequestsError
from datetime import datetime, timezone
from urllib.parse import quote

from flask import request, jsonify, Response as FlaskResponse

from .app_blueprint import app_routes_bp
from .. import csrf
from ..app_helpers import (
    acquire_search_slot,
    release_search_slot,
    MAX_CONCURRENT_SEARCHES_PER_USER,
    lookup_email,
    lookup_ip,
    lookup_domain,
    _get_overheid_key,
    _get_hibp_key,
    WEBCAM_DATA,
    search_email_async,
    search_username_async,
    get_sherlock_sites,
    search_email_holehe,
    search_email_combined,
    search_username,
)
from cms.rate_limiting import rate_limit, DEFAULT_RATE_LIMIT, STRICT_RATE_LIMIT
from cms.api_key_auth import api_key_required
from cms.feature_flags import tool_enabled
from cms.validation import validate
from cms.cache import get as cache_get, set as cache_set
from cms.sse_utils import run_sse_search

from cms.validation import (
    PersonSearchSchema,
    EmailQuerySchema,
    IPQuerySchema,
    DomainQuerySchema,
    OpenKVKQuerySchema,
    WebcamQuerySchema,
    HIBPQuerySchema,
    UsernameQuerySchema,
    EmailStreamSchema,
    EmailHoleheSchema,
    EmailCombinedSchema,
    EmailCrossValidatedSchema,
    UsernameRapidAPISchema,
)
from cms.services.search_service import search_person
from search_history import search_history

from .response import api_error

logger = logging.getLogger(__name__)


# =============================================================================
# OSINT Person Search Stream
# =============================================================================


@app_routes_bp.route("/api/person/stream", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("username")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="person")
@validate(PersonSearchSchema)
def person_search_stream() -> FlaskResponse:
    name = request.validated_data.get("name", "")
    if not name:
        return api_error("Name required", 400)

    from flask_login import current_user

    if (
        current_user
        and current_user.is_authenticated
        and not acquire_search_slot(current_user.id)
    ):
        return jsonify(
            {
                "error": f"Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete."
            }
        ), 429

    def search_worker(q, stop_event):
        try:
            result = search_person(name)
            q.put({"complete": True, "result": result})
        except Exception:
            logger.exception("Person search failed")
            q.put({"complete": True, "error": "Search error"})
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    return run_sse_search(search_worker)


@app_routes_bp.route("/api/person", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("username")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="person")
@validate(PersonSearchSchema)
def person_search_json() -> FlaskResponse:
    """Synchronous person search returning JSON (not streaming)."""
    from cms.services.search_service import search_person

    name = request.validated_data.get("name", "")
    if not name:
        return api_error("Name required", 400)
    result = search_person(name)
    return jsonify(result)


# =============================================================================
# IP / Email / Domain Lookup
# =============================================================================


@app_routes_bp.route("/api/email", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("email")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="email")
@validate(EmailQuerySchema)
def email_lookup() -> FlaskResponse:
    email = request.validated_data.get("email", "")
    if not email:
        return api_error("Email required", 400)
    cached = cache_get("email", email)
    if cached:
        return jsonify(cached)
    result = lookup_email(email)
    cache_set("email", email, result, timeout=300)
    return jsonify(result)


@app_routes_bp.route("/api/ip", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("ip")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="ip")
@validate(IPQuerySchema)
def ip_lookup() -> FlaskResponse:
    ip = request.validated_data.get("ip", "")
    if not ip:
        return api_error("IP address required", 400)
    cached = cache_get("ip", ip)
    if cached:
        return jsonify(cached)
    result = lookup_ip(ip)
    cache_set("ip", ip, result, timeout=3600)
    return jsonify(result)


@app_routes_bp.route("/api/domain", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("domain")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="domain")
@validate(DomainQuerySchema)
def domain_lookup() -> FlaskResponse:
    domain = request.validated_data.get("domain", "")
    if not domain:
        return api_error("Domain required", 400)
    cached = cache_get("domain", domain)
    if cached:
        return jsonify(cached)
    result = lookup_domain(domain)
    cache_set("domain", domain, result, timeout=3600)
    return jsonify(result)


# =============================================================================
# OpenKVK (Dutch Business Registry)
# =============================================================================


@app_routes_bp.route("/api/openkvk", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("openkvk")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="openkvk")
@validate(OpenKVKQuerySchema)
def openkvk_lookup() -> FlaskResponse:
    query = request.validated_data.get("query", "")
    if not query:
        return api_error("Company name, KVK number, or postcode required", 400)

    cached = cache_get("openkvk", query)
    if cached:
        return jsonify(cached)

    api_key = _get_overheid_key()
    result = {"query": query, "results": [], "error": None, "configured": bool(api_key)}

    if not api_key:
        result["error"] = "Overheid.io API key not configured"
        result["setup_hint"] = (
            "Set via Settings > API Keys (overheid_api_key) or OVERHEID_API_KEY env var. Get free key at https://overheid.io"
        )
        return jsonify(result)

    try:
        clean_query = quote(query)
        search_url = f"https://api.overheid.io/v3/openkvk?query={clean_query}&size=20"

        headers = {"Accept": "application/json", "ovio-api-key": api_key}

        response = jittered_get(search_url, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()
            bedrijven = data.get("_embedded", {}).get("bedrijf", [])

            for company in bedrijven:
                slug = company.get("_links", {}).get("self", {}).get("href", "")
                if slug:
                    detail_url = f"https://api.overheid.io{slug}"
                    try:
                        detail_resp = jittered_get(
                            detail_url, headers=headers, timeout=10
                        )
                        if detail_resp.status_code == 200:
                            detail = detail_resp.json()
                            company.update(detail)
                    except RequestsError as e:
                        logger.debug(
                            f"OpenKVK detail fetch failed ({type(e).__name__}): {e}"
                        )

                result["results"].append(
                    {
                        "kvknummer": company.get("kvkNummer")
                        or company.get("kvknummer"),
                        "naam": company.get("naam")
                        or (
                            company.get("huidigeHandelsNamen", [""])[0]
                            if company.get("huidigeHandelsNamen")
                            else ""
                        ),
                        "handelsnamen": company.get("huidigeHandelsNamen", []),
                        "rechtsvorm": company.get("rechtsvormOmschrijving"),
                        "activiteit": company.get("activiteitomschrijving"),
                        "sbi_codes": company.get("sbi", []),
                        "website": company.get("website"),
                        "bezoekadres": None,
                        "postcode": None,
                        "plaats": None,
                        "land": None,
                        "coords": None,
                        "inschrijvingstype": company.get("inschrijvingstype"),
                        "actief": company.get("actief", True),
                        "vestigingsnummer": company.get("vestigingsnummer"),
                        "updated_at": company.get("updated_at"),
                        "details_url": slug,
                    }
                )

                bezoek = company.get("bezoeklocatie", {})
                if bezoek:
                    addr = bezoek.get("straat", "")
                    huisnr = bezoek.get("huisnummer", "")
                    result["results"][-1]["bezoekadres"] = f"{addr} {huisnr}".strip()
                    result["results"][-1]["postcode"] = bezoek.get("postcode")
                    result["results"][-1]["plaats"] = bezoek.get("plaats")
                    result["results"][-1]["land"] = bezoek.get("land")

                loc = company.get("locatie", {})
                if loc:
                    result["results"][-1]["coords"] = {
                        "lat": loc.get("lat"),
                        "lon": loc.get("lon"),
                    }

            result["total"] = data.get("totalItemCount", len(result["results"]))

        elif response.status_code == 404:
            result["error"] = "No results found"
        else:
            result["error"] = f"API error: {response.status_code}"

    except RequestsError as e:
        if "timeout" in str(e).lower():
            result["error"] = "Request timed out"
        else:
            logger.exception("OpenKVK request failed")
            result["error"] = "Request failed"
    except (ValueError, KeyError):
        logger.exception("OpenKVK unexpected error")
        result["error"] = "Unexpected error"

    search_history.add_entry(
        "openkvk",
        query,
        f"{len(result['results'])} results found",
        len(result["results"]),
    )

    return jsonify(result)


# =============================================================================
# Webcam Lookup
# =============================================================================


@app_routes_bp.route("/api/webcam", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("webcam")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="webcam")
@validate(WebcamQuerySchema)
def webcam_lookup() -> FlaskResponse:
    query = request.validated_data.get("query", "").lower().strip()
    country_code = request.validated_data.get("country", "").lower().strip()

    webcams = WEBCAM_DATA["webcams"].copy()
    results = []
    selected_country = None

    country_map = {
        "united states": "us",
        "usa": "us",
        "us": "us",
        "america": "us",
        "united kingdom": "uk",
        "uk": "uk",
        "britain": "uk",
        "england": "uk",
        "netherlands": "nl",
        "holland": "nl",
        "nl": "nl",
        "germany": "de",
        "deutschland": "de",
        "de": "de",
        "france": "fr",
        "fr": "fr",
        "japan": "jp",
        "jp": "jp",
        "australia": "au",
        "au": "au",
        "canada": "ca",
        "ca": "ca",
        "italy": "it",
        "it": "it",
        "spain": "es",
        "es": "es",
    }

    if query in country_map:
        country_code = country_map[query]
        query = ""

    if country_code:
        results = [w for w in webcams if w["country"] == country_code]
        selected_country = next(
            (c for c in WEBCAM_DATA["countries"] if c["code"] == country_code), None
        )
    elif query:
        results = [
            w
            for w in webcams
            if query in w["city"].lower()
            or query in w["country"].lower()
            or query in w["title"].lower()
            or query in w["location"].lower()
        ]
    else:
        results = webcams

    return jsonify(
        {
            "webcams": results[:24],
            "countries": WEBCAM_DATA["countries"],
            "selected_country": selected_country,
        }
    )


# =============================================================================
# HIBP (Have I Been Pwned)
# =============================================================================


@app_routes_bp.route("/api/hibp", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("hibp")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="hibp")
@validate(HIBPQuerySchema)
def hibp_check() -> FlaskResponse:
    email = request.validated_data.get("email", "")
    if not email:
        return api_error("Email required", 400)

    cached = cache_get("hibp", email)
    if cached:
        return jsonify(cached)

    hibp_key = _get_hibp_key()
    if not hibp_key:
        return jsonify({"email": email, "no_api_key": True, "breaches": []})

    try:
        headers = {"User-Agent": "OSINT-Dashboard", "hibp-api-key": hibp_key}

        response = jittered_get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}?truncateResponse=false",
            headers=headers,
            timeout=15,
        )

        if response.status_code == 200:
            breaches = response.json()
            result = {"email": email, "found": True, "breaches": breaches}
            cache_set("hibp", email, result, timeout=3600)
            return jsonify(result)
        elif response.status_code == 404:
            result = {"email": email, "found": False, "breaches": []}
            cache_set("hibp", email, result, timeout=3600)
            return jsonify(result)
        elif response.status_code == 401:
            return jsonify(
                {
                    "email": email,
                    "error": "Invalid API key",
                    "no_api_key": True,
                    "breaches": [],
                }
            )
        elif response.status_code == 429:
            return jsonify({"email": email, "error": "Rate limited", "breaches": []})
        else:
            return jsonify(
                {
                    "email": email,
                    "error": f"API error: {response.status_code}",
                    "breaches": [],
                }
            )

    except (RequestsError, ValueError):
        logger.exception("HIBP check error")
        return jsonify(
            {"email": email, "error": "Internal server error", "breaches": []}
        )


# =============================================================================
# Username Stream
# =============================================================================


@app_routes_bp.route("/api/username/stream", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("username")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="username_stream")
@validate(UsernameQuerySchema)
def username_search_stream() -> FlaskResponse:
    username = request.validated_data.get("username", "")
    if not username:
        return api_error("Username required", 400)

    from flask_login import current_user

    if (
        current_user
        and current_user.is_authenticated
        and not acquire_search_slot(current_user.id)
    ):
        return jsonify(
            {
                "error": f"Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete."
            }
        ), 429

    email_sites = get_sherlock_sites()

    if not email_sites:
        return api_error("Could not load site data", 400)

    def search_worker(q, stop_event):
        progress_state = {
            "checked": 0,
            "found": 0,
            "current_site": "",
            "total": len(email_sites),
        }

        def progress_callback(progress):
            if stop_event.is_set():
                return
            progress_state.update(progress)
            total = progress_state["total"]
            checked = progress_state["checked"]
            found = progress_state["found"]
            current_site = progress_state["current_site"]
            q.put(
                {
                    "progress": {
                        "checked": checked,
                        "total": total,
                        "found": found,
                        "percent": int((checked / total) * 100) if total > 0 else 0,
                        "current_site": current_site,
                    }
                }
            )

        try:
            result = asyncio.run(search_username_async(username, progress_callback))
            found_count = result.get("found_count", 0)
            search_history.add_entry(
                "username", username, f"{found_count} accounts found", found_count
            )
            q.put({"complete": True, "result": result})
        except Exception:
            logger.exception("Username search failed")
            q.put({"error": "Search error"})
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    return run_sse_search(search_worker)


# =============================================================================
# Email Stream
# =============================================================================


@app_routes_bp.route("/api/email/stream", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("email")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="email_stream")
@validate(EmailStreamSchema)
def email_search_stream() -> FlaskResponse:
    email = request.validated_data.get("email", "")
    tags = request.validated_data.get("tags", ["all"])
    if not email:
        return api_error("Email required", 400)

    from flask_login import current_user

    if (
        current_user
        and current_user.is_authenticated
        and not acquire_search_slot(current_user.id)
    ):
        return jsonify(
            {
                "error": f"Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete."
            }
        ), 429

    limit = 30
    for tag in tags:
        if isinstance(tag, str) and tag.isdigit():
            limit = max(limit, int(tag))
        elif isinstance(tag, int):
            limit = max(limit, tag)
        elif tag in ["social", "30"]:
            limit = max(limit, 30)
        elif tag in ["developer", "50"]:
            limit = max(limit, 50)
        elif tag in ["gaming", "100"]:
            limit = max(limit, 100)
        elif tag in ["all", "200"]:
            limit = max(limit, 200)

    email_sites = get_sherlock_sites()

    if not email_sites:
        return api_error("Could not load site data", 400)

    def search_worker(q, stop_event):
        progress_state = {
            "checked": 0,
            "found": 0,
            "current_site": "",
            "total": len(email_sites),
        }

        def progress_callback(progress):
            progress_state.update(progress)
            total = progress_state["total"]
            checked = progress_state["checked"]
            found = progress_state["found"]
            current_site = progress_state["current_site"]
            q.put(
                {
                    "progress": {
                        "checked": checked,
                        "total": total,
                        "found": found,
                        "percent": int((checked / total) * 100) if total > 0 else 0,
                        "current_site": current_site,
                    }
                }
            )

        try:
            result = asyncio.run(search_email_async(email, progress_callback, limit))
            found_count = result.get("found_count", 0)
            search_history.add_entry(
                "email", email, f"{found_count} accounts found", found_count
            )
            q.put({"complete": True, "result": result})
        except Exception:
            logger.exception("Email Sherlock search failed")
            q.put({"error": "Search error"})
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    return run_sse_search(search_worker)


# =============================================================================
# Email Holehe
# =============================================================================


@app_routes_bp.route("/api/email/holehe", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("email_holehe")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="email_holehe")
@validate(EmailHoleheSchema)
def email_holehe() -> FlaskResponse:
    from argparse import Namespace

    email = request.validated_data.get("email", "")
    if not email:
        return api_error("Email required", 400)

    from flask_login import current_user

    if (
        current_user
        and current_user.is_authenticated
        and not acquire_search_slot(current_user.id)
    ):
        return jsonify(
            {
                "error": f"Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete."
            }
        ), 429

    from holehe.core import import_submodules, get_functions

    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    total_websites = len(websites)

    def search_worker(q, stop_event):
        progress_state = {
            "checked": 0,
            "found": 0,
            "current_site": "",
            "total": total_websites,
        }

        def progress_callback(progress):
            if stop_event.is_set():
                return
            progress_state.update(progress)
            total = progress_state["total"]
            checked = progress_state["checked"]
            found = progress_state["found"]
            current_site = progress_state["current_site"]
            q.put(
                {
                    "progress": {
                        "checked": checked,
                        "total": total,
                        "found": found,
                        "percent": int((checked / total) * 100) if total > 0 else 0,
                        "current_site": current_site,
                    }
                }
            )

        try:
            result = asyncio.run(search_email_holehe(email, progress_callback))
            search_history.add_entry(
                "holehe",
                email,
                f"{result.get('found_count', 0)} accounts found",
                result.get("found_count", 0),
            )
            q.put({"complete": True, "result": result})
        except Exception:
            logger.exception("Email Holehe search failed")
            q.put({"error": "Search error"})
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    return run_sse_search(search_worker)


# =============================================================================
# Email Combined
# =============================================================================


@app_routes_bp.route("/api/email/combined", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("email_holehe")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="email_combined")
@validate(EmailCombinedSchema)
def email_combined() -> FlaskResponse:
    email = request.validated_data.get("email", "")
    if not email:
        return api_error("Email required", 400)

    from flask_login import current_user

    if (
        current_user
        and current_user.is_authenticated
        and not acquire_search_slot(current_user.id)
    ):
        return jsonify(
            {
                "error": f"Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete."
            }
        ), 429

    from holehe.core import import_submodules, get_functions
    from argparse import Namespace

    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    sherlock_sites = get_sherlock_sites() or {}
    holehe_total = len(websites)
    sherlock_total = len(sherlock_sites)
    total_sites = sherlock_total + holehe_total

    def search_worker(q, stop_event):
        progress_state = {
            "checked": 0,
            "found": 0,
            "current_site": "",
            "total": total_sites,
        }

        def progress_callback(progress):
            if stop_event.is_set():
                return
            saved_total = progress_state["total"]
            progress_state.update(progress)
            if progress.get("total", 0) < saved_total:
                progress_state["total"] = saved_total
            total = progress_state["total"]
            checked = progress_state["checked"]
            found = progress_state["found"]
            current_site = progress_state["current_site"]
            q.put(
                {
                    "progress": {
                        "checked": checked,
                        "total": total,
                        "found": found,
                        "percent": int((checked / total) * 100) if total > 0 else 0,
                        "current_site": current_site,
                    }
                }
            )

        try:
            result = asyncio.run(search_email_combined(email, progress_callback))
            found_count = result.get("found_count", 0)
            search_history.add_entry(
                "email",
                email,
                f"{found_count} accounts found (Sherlock + Holehe)",
                found_count,
            )
            q.put({"complete": True, "result": result})
        except Exception:
            logger.exception("Email combined search failed")
            q.put({"error": "Search error"})
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    return run_sse_search(search_worker)


# =============================================================================
# Email Cross-validated
# =============================================================================


@app_routes_bp.route("/api/email/crossvalidated", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("email_holehe")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="email_crossvalidated")
@validate(EmailCrossValidatedSchema)
def email_cross_validated() -> FlaskResponse:
    email = request.validated_data.get("email", "")
    if not email:
        return api_error("Email required", 400)

    from flask_login import current_user

    if (
        current_user
        and current_user.is_authenticated
        and not acquire_search_slot(current_user.id)
    ):
        return jsonify(
            {
                "error": f"Maximum {MAX_CONCURRENT_SEARCHES_PER_USER} concurrent searches allowed. Please wait for running searches to complete."
            }
        ), 429

    from holehe.core import import_submodules, get_functions
    from argparse import Namespace

    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    sherlock_sites = get_sherlock_sites() or {}
    holehe_total = len(websites)
    sherlock_total = len(sherlock_sites)
    total_sites = sherlock_total + holehe_total

    def search_worker(q, stop_event):
        progress_state = {
            "checked": 0,
            "found": 0,
            "current_site": "",
            "total": total_sites,
        }

        def progress_callback(progress):
            if stop_event.is_set():
                return
            saved_total = progress_state["total"]
            progress_state.update(progress)
            if progress.get("total", 0) < saved_total:
                progress_state["total"] = saved_total
            total = progress_state["total"]
            checked = progress_state["checked"]
            found = progress_state["found"]
            current_site = progress_state["current_site"]
            q.put(
                {
                    "progress": {
                        "checked": checked,
                        "total": total,
                        "found": found,
                        "percent": int((checked / total) * 100) if total > 0 else 0,
                        "current_site": current_site,
                    }
                }
            )

        try:
            result = asyncio.run(search_email_combined(email, progress_callback))
            found_count = result.get("found_count", 0)
            cross_count = result.get("cross_validated_count", 0)
            search_history.add_entry(
                "email",
                email,
                f"{found_count} found, {cross_count} cross-validated",
                found_count,
            )
            q.put({"complete": True, "result": result})
        except Exception:
            logger.exception("Email cross-validated search failed")
            q.put({"error": "Search error"})
        finally:
            if current_user and current_user.is_authenticated:
                release_search_slot(current_user.id)

    return run_sse_search(search_worker)


# =============================================================================
# Username Search
# =============================================================================


@app_routes_bp.route("/api/username", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("username")
@rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="username")
@validate(UsernameQuerySchema)
def username_search() -> FlaskResponse:
    username = request.validated_data.get("username", "")
    if not username:
        return api_error("Username required", 400)
    return jsonify(search_username(username))


# =============================================================================
# Username RapidAPI
# =============================================================================


@app_routes_bp.route("/api/username/rapidapi", methods=["POST"])
@api_key_required
@tool_enabled("username")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="username_rapidapi")
@validate(UsernameRapidAPISchema)
def username_rapidapi() -> FlaskResponse:
    """Check username availability via RapidAPI, fallback to Sherlock."""
    from cms.models import Setting

    username = request.validated_data.get("username", "")
    if not username:
        return api_error("Username required", 400)

    USAGE_LIMIT = 100
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    stored_month = Setting.get("rapidapi_username_month")
    used_count = (
        int(Setting.get("rapidapi_username_used") or "0")
        if stored_month == now_month
        else 0
    )
    api_key = Setting.get("rapidapi_username_key")

    usage_info = {
        "used": used_count,
        "limit": USAGE_LIMIT,
        "remaining": max(0, USAGE_LIMIT - used_count),
    }

    if api_key and used_count < USAGE_LIMIT:
        try:
            headers = {
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": "osint-username-availability-brand-checker-api.p.rapidapi.com",
            }
            resp = jittered_get(
                f"https://osint-username-availability-brand-checker-api.p.rapidapi.com/check?username={username}",
                headers=headers,
                timeout=15,
            )
            data = resp.json()

            Setting.set("rapidapi_username_month", now_month)
            Setting.set("rapidapi_username_used", str(used_count + 1))
            usage_info["used"] = used_count + 1
            usage_info["remaining"] = max(0, USAGE_LIMIT - used_count - 1)

            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                results_list = data.get("results", [])
                taken = [
                    r
                    for r in results_list
                    if r.get("available") is True and r.get("url")
                ]
                if taken:
                    fp_patterns = [
                        rb"\b(not found|no results?|doesn\'t exist|profile not found)\b",
                        rb"\b(404|page not found|this page doesn\'t exist)\b",
                        rb"\b(invalid user|user invalid|username not|user not found)\b",
                        rb"\b(removed this|content removed|deleted account|this account)\b",
                        rb"(sign up|create account|log in|login).{0,50}(to view|to see)",
                        rb"(view profile|profile).{0,30}(requires|need).{0,30}(login|sign in)",
                        rb"\b(error|404|403|400)\b.{0,20}\b(page|content)",
                    ]
                    fp_compiled = [re.compile(p, re.I) for p in fp_patterns]

                    def _verify(url, timeout=5):
                        try:
                            r = jittered_get(
                                url,
                                timeout=timeout,
                                allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0"},
                            )
                            body = r.content[:2048].lower()
                            for pat in fp_compiled:
                                if pat.search(body):
                                    return False, "likely_false_positive"
                            username_bytes = username.lower().encode()
                            if username_bytes in body:
                                return True, "verified"
                            if len(body) < 500:
                                return False, "too_small"
                            return True, "unconfirmed"
                        except Exception as e:
                            logger.debug(
                                f"RapidAPI verify failed ({type(e).__name__}): {e}"
                            )
                            return None, "unreachable"

                    with ThreadPoolExecutor(max_workers=10) as pool:
                        fut_map = {pool.submit(_verify, r["url"]): r for r in taken}
                        for fut in as_completed(fut_map):
                            r = fut_map[fut]
                            verified, note = fut.result()
                            if verified is False:
                                r["available"] = False
                            r["verified"] = verified
                            r["verification_note"] = note
            except Exception as ve:
                logger.warning(f"RapidAPI verification failed: {ve}")

            return jsonify(
                {
                    "source": "rapidapi",
                    "username": username,
                    "results": data,
                    "api_usage": usage_info,
                }
            )
        except (RequestsError, ValueError) as e:
            logger.error(
                f"RapidAPI username check failed for '{username}' ({type(e).__name__}): {e}"
            )

    usage_info["note"] = "Monthly limit reached or API not configured - use Sherlock"
    return jsonify(
        {
            "source": "sherlock",
            "username": username,
            "fallback_to_sherlock": True,
            "api_usage": usage_info,
        }
    )


@app_routes_bp.route("/api/username/rapidapi-status", methods=["GET"])
def username_rapidapi_status() -> FlaskResponse:
    """Return current RapidAPI usage status."""
    from cms.models import Setting

    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    stored_month = Setting.get("rapidapi_username_month")
    used_count = (
        int(Setting.get("rapidapi_username_used") or "0")
        if stored_month == now_month
        else 0
    )
    api_key = Setting.get("rapidapi_username_key")
    return jsonify(
        {
            "configured": bool(api_key),
            "used": used_count,
            "limit": 100,
            "remaining": max(0, 100 - used_count),
        }
    )
