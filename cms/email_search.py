import asyncio
import logging
import os
import re
import socket
import time

from curl_cffi import requests as curl_requests
from curl_cffi import CurlError

from cms.constants import HEADERS
from cms.validators import validate_email, interpolate_string
from cms.cache_utils import get_cached_result, set_cached_result
from cms.search_tracking import increment_request_count
from cms.sherlock_utils import get_sherlock_sites

logger = logging.getLogger(__name__)


async def _dummy_check(site_name):
    """Return an immediate rate-limited placeholder, no I/O."""
    return {
        "site": site_name,
        "name": site_name,
        "exists": None,
        "status": "rate_limited",
        "rateLimit": True,
    }


async def check_site_with_retry(client, site_name, site_info, email, max_retries=None):
    from cms.rate_limiting import (
        RETRY_MAX_ATTEMPTS,
        RETRY_BASE_DELAY,
        RATE_LIMIT_STATUS_CODES,
        set_rate_limited,
    )

    if max_retries is None:
        max_retries = RETRY_MAX_ATTEMPTS
    for attempt in range(max_retries):
        try:
            result = await check_email_site(client, site_name, site_info, email)
            http_status = result.get("http_status") or result.get("status_code")
            if http_status in RATE_LIMIT_STATUS_CODES:
                set_rate_limited(site_name, retry_after=60 * (attempt + 1))
                if attempt < max_retries - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
            if result.get("rateLimit"):
                set_rate_limited(site_name, retry_after=30)
                if attempt < max_retries - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
            result["attempts"] = attempt + 1
            result["retried"] = attempt > 0
            return result
        except Exception:
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                await asyncio.sleep(delay)
                continue
            logger.exception("Email site check failed: %s", site_name)
            return {
                "site": site_name,
                "name": site_name,
                "exists": False,
                "status": "error",
                "error_message": "Site check failed",
                "attempts": max_retries,
                "retried": attempt > 0,
            }
    return {
        "site": site_name,
        "name": site_name,
        "exists": False,
        "status": "failed_after_retries",
        "attempts": max_retries,
    }


def check_site_email(client, email, site_info):
    name, url, check_function = site_info
    try:
        return asyncio.run(check_function(client, email, url))
    except Exception:
        logger.exception("Sync email site check failed: %s", name)
        return {
            "name": name,
            "domain": url,
            "exists": False,
            "rateLimit": False,
            "error": "Check failed",
        }


EMAIL_FALSE_POSITIVE_PATTERNS = re.compile(
    r"\b(not found|no results?|doesn\'t exist|not exist|user not found|"
    r"profile not found|account not found|page not found|404|"
    r"invalid user|user invalid|user does not|username not|"
    r"sign up|create account|log in|login|"
    r"this (page|profile|account) (is|isn\'t|is not|has been|was) (not|removed|deleted|available)|"
    r"(this page|these profiles?) (contain no|has no|have no) (results|posts?|content)|"
    r"deze (pagina|profiel|account) is niet (beschikbaar|gevonden)|"
    r"the (requested|specified) (user|account|profile) (could not|cannot) be found|"
    r"(pagina|profiel|account) is niet (beschikbaar|gevonden)|"
    r"this page (doesn\'t|does not) exist|"
    r"(page|profile|account) (removed|deleted|not available)|"
    r"sorry, this page|the link may be broken|page could not be found"
    r")\b",
    re.I,
)


async def check_email_site(client, site_name, site_info, email):
    finding = {
        "name": site_name,
        "domain": site_info.get("urlMain", site_info.get("url", "")),
        "exists": None,
        "rateLimit": False,
        "status": "checking",
    }
    probe_url = site_info.get("urlProbe") or site_info.get("url", "")
    if "@" in email and "{}" in probe_url and "?" not in probe_url.split("{}")[0]:
        username = email.split("@")[0]
    else:
        username = email
    url = interpolate_string(probe_url, username)
    finding["url"] = interpolate_string(site_info.get("url", ""), username)
    try:
        response = await client.get(url, headers=HEADERS, timeout=15)
        finding["http_status"] = response.status_code

        if response.status_code != 200:
            finding["exists"] = False
            finding["status"] = "not_found"
            return finding

        response_text = response.text
        if isinstance(response_text, bytes):
            response_text = response_text.decode("utf-8", errors="replace")
        text_lower = response_text.lower()

        if (
            EMAIL_FALSE_POSITIVE_PATTERNS.search(text_lower)
            and len(response_text) < 10000
        ):
            finding["exists"] = False
            finding["status"] = "not_found"
            finding["matched_pattern"] = "false_positive"
            return finding

        email_lower = email.lower()
        email_local = email_lower.split("@")[0]

        if email_lower in text_lower:
            finding["exists"] = True
            finding["status"] = "confirmed"
            finding["verified"] = True
        elif email_local in text_lower:
            finding["exists"] = True
            finding["status"] = "confirmed"
            finding["verified"] = True
        else:
            finding["exists"] = None
            finding["status"] = "unverified"
            finding["verification"] = "no_content_match"
    except CurlError as e:
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            finding["status"] = "timeout"
            finding["rateLimit"] = True
        elif "connection" in str(e).lower() or "couldn't connect" in str(e).lower():
            finding["status"] = "connection_error"
        else:
            finding["status"] = "error"
    except Exception:
        finding["status"] = "error"
    return finding


async def search_email_async(email, progress_callback=None, limit=30):
    from cms.rate_limiting import (
        is_rate_limited,
        set_rate_limited,
        get_rate_limit_status,
    )

    cache_key = f"email_sherlock_{limit}"
    cached = get_cached_result(cache_key, email)
    if cached:
        cached["from_cache"] = True
        return cached

    increment_request_count("email_sherlock")

    result = {
        "email": email,
        "valid_format": validate_email(email),
        "provider": email.split("@")[1] if "@" in email else None,
        "mx_records": None,
        "disposable": False,
        "account_checks": [],
        "search_links": [],
        "rate_limit_status": [],
        "retried_checks": 0,
        "from_cache": False,
    }

    if not result["valid_format"]:
        return result

    domain = result["provider"]

    try:
        mx_records = socket.getaddrinfo(domain, 25)
        result["mx_records"] = [r[3][0] for r in mx_records[:3]]
    except socket.gaierror:
        result["mx_records"] = []
    except Exception as e:
        logger.debug(f"MX lookup failed ({type(e).__name__}): {e}")
        result["mx_records"] = []

    disposable_domains = [
        "tempmail.com",
        "guerrillamail.com",
        "mailinator.com",
        "10minutemail.com",
        "throwaway.email",
        "temp-mail.org",
        "fakeinbox.com",
        "maildrop.cc",
        "yopmail.com",
        "sharklasers.com",
    ]
    result["disposable"] = any(d in domain.lower() for d in disposable_domains)

    email_sites = get_sherlock_sites()

    priority_sites = [
        "Facebook",
        "Instagram",
        "Twitter",
        "TikTok",
        "LinkedIn",
        "YouTube",
        "WhatsApp",
        "Telegram",
        "Snapchat",
        "Pinterest",
        "Reddit",
        "GitHub",
        "Dropbox",
        "Google",
        "Microsoft",
        "Apple",
        "Amazon",
        "Netflix",
        "Spotify",
        "Adobe",
        "Discord",
        "Slack",
        "Zoom",
        "PayPal",
        "Steam",
        "Ebay",
        "Airbnb",
        "Uber",
        "Tinder",
        "Bumble",
    ]

    if limit <= 50:
        priority = {k: v for k, v in email_sites.items() if k in priority_sites}
        remaining = {k: v for k, v in email_sites.items() if k not in priority_sites}
        combined = {**priority, **remaining}
        email_sites = dict(list(combined.items())[:limit])
    else:
        email_sites = dict(list(email_sites.items())[:limit])

    all_checks = []
    total_sites = len(email_sites)
    checked = 0
    found_count = 0
    retried_count = 0
    rate_limits_hit = []
    batch_size = 30

    conn_timeout = float(os.environ.get("EMAIL_SEARCH_TIMEOUT", "60"))
    start_time = time.time()

    async with curl_requests.AsyncSession(
        impersonate="chrome124", timeout=30
    ) as client:
        site_items = list(email_sites.items())

        for i in range(0, total_sites, batch_size):
            batch = site_items[i : i + batch_size]
            tasks = []
            site_names = []
            for site_name, site_info in batch:
                site_names.append(site_name)
                limited, limit_data = is_rate_limited(site_name)
                if limited:
                    rate_limits_hit.append(
                        {"site": site_name, "wait": limit_data["reset_at"]}
                    )
                    tasks.append(_dummy_check(site_name))
                else:
                    tasks.append(
                        check_site_with_retry(client, site_name, site_info, email)
                    )

            results = await asyncio.gather(
                *[asyncio.wait_for(t, timeout=15) for t in tasks],
                return_exceptions=True,
            )

            for site_name, r in zip(site_names, results):
                if isinstance(r, Exception):
                    all_checks.append(
                        {"site": site_name, "exists": False, "status": "error"}
                    )
                else:
                    if r.get("retried"):
                        retried_count += 1
                    if r.get("rateLimit"):
                        set_rate_limited(site_name)
                    all_checks.append(r)
                    if r.get("exists") == True:
                        found_count += 1
                checked += 1
                if progress_callback:
                    progress_callback(
                        {
                            "checked": checked,
                            "total": total_sites,
                            "found": found_count,
                            "percent": int((checked / total_sites) * 100),
                            "current_site": site_name,
                        }
                    )

            elapsed = time.time() - start_time
            if elapsed > conn_timeout:
                logger.warning(
                    "Email search timed out after %ss, stopping early (checked %d/%d)",
                    conn_timeout,
                    checked,
                    total_sites,
                )
                break

    result["account_checks"] = all_checks
    result["found_count"] = sum(1 for c in all_checks if c.get("exists") == True)
    result["rate_limited"] = sum(1 for c in all_checks if c.get("rateLimit") == True)
    result["retried_checks"] = retried_count
    result["rate_limit_sites"] = get_rate_limit_status()

    result["search_links"] = [
        {"name": "Hunter.io", "url": f"https://hunter.io/search/{email}"},
        {"name": "EmailRep", "url": f"https://emailrep.io/{email}"},
        {
            "name": "Have I Been Pwned",
            "url": f"https://haveibeenpwned.com/unverifiedpwned?q={email}",
        },
        {"name": "Google", "url": f'https://www.google.com/search?q="{email}"'},
        {"name": "Dehashed", "url": f"https://dehashed.com/search?query={email}"},
    ]

    set_cached_result(cache_key, email, result.copy())
    return result


def lookup_email(email):
    return asyncio.run(search_email_async(email))


async def search_email_holehe(email, progress_callback=None):
    from holehe.core import launch_module, import_submodules, get_functions
    from argparse import Namespace

    result = {
        "email": email,
        "valid_format": validate_email(email),
        "method": "holehe",
        "holehe_results": [],
        "found_count": 0,
        "rate_limited_count": 0,
    }

    if not result["valid_format"]:
        return result

    out = []
    checked = 0
    modules = import_submodules("holehe.modules")
    args = Namespace(nopasswordrecovery=False)
    websites = get_functions(modules, args)
    total = len(websites)

    async with curl_requests.AsyncSession(
        impersonate="chrome124", timeout=30
    ) as client:
        for website in websites:
            website_name = website.__name__
            try:
                await launch_module(website, email, client, out)
                checked += 1
                if progress_callback:
                    progress_callback(
                        {
                            "checked": checked,
                            "total": total,
                            "found": len([x for x in out if x.get("exists")]),
                            "percent": int((checked / total) * 100),
                            "current_site": website_name,
                        }
                    )
            except Exception:
                checked += 1
                out.append({"name": website_name, "exists": False, "error": True})

    found = []
    rate_limited = []
    not_found = []

    for item in out:
        site_data = {
            "site": item.get("name", item.get("Name", "Unknown")),
            "domain": item.get("domain", ""),
            "exists": item.get("exists", False),
            "rateLimit": item.get("rateLimit", False),
            "error": item.get("error", False),
            "emailrecovery": item.get("emailrecovery", None),
            "phoneNumber": item.get("phoneNumber", None),
            "details": item.get("details", {}),
        }
        if item.get("exists"):
            found.append(site_data)
        elif item.get("rateLimit"):
            rate_limited.append(site_data)
        else:
            not_found.append(site_data)

    result["holehe_results"] = out
    result["found"] = found
    result["rate_limited"] = rate_limited
    result["not_found"] = not_found
    result["found_count"] = len(found)
    result["rate_limited_count"] = len(rate_limited)
    result["total_checked"] = len(out)
    return result


def lookup_email_holehe(email):
    return asyncio.run(search_email_holehe(email))


async def search_email_combined(email, progress_callback=None):
    sherlock_result = None
    holehe_result = None
    sherlock_done = False
    holehe_done = False

    async def run_sherlock():
        nonlocal sherlock_result, sherlock_done
        try:
            sherlock_result = await search_email_async(email, progress_callback)
        except Exception:
            logger.exception("Sherlock combined search failed")
            sherlock_result = {"error": "Sherlock search failed"}
        finally:
            sherlock_done = True

    async def run_holehe():
        nonlocal holehe_result, holehe_done
        try:
            holehe_result = await search_email_holehe(email, progress_callback)
        except Exception:
            logger.exception("Holehe combined search failed")
            holehe_result = {"error": "Holehe search failed"}
        finally:
            holehe_done = True

    await asyncio.gather(run_sherlock(), run_holehe())

    combined = {
        "email": email,
        "valid_format": validate_email(email),
        "provider": email.split("@")[1] if "@" in email else None,
        "mx_records": None,
        "disposable": False,
        "search_links": [],
    }

    if combined["valid_format"]:
        domain = combined["provider"]
        try:
            mx_records = socket.getaddrinfo(domain, 25)
            combined["mx_records"] = [r[3][0] for r in mx_records[:3]]
        except Exception:
            combined["mx_records"] = []
        disposable_domains = [
            "tempmail.com",
            "guerrillamail.com",
            "mailinator.com",
            "10minutemail.com",
            "throwaway.email",
            "temp-mail.org",
            "fakeinbox.com",
            "maildrop.cc",
            "yopmail.com",
            "sharklasers.com",
        ]
        combined["disposable"] = any(d in domain.lower() for d in disposable_domains)

    combined["sherlock"] = sherlock_result or {"error": "Sherlock search failed"}
    combined["holehe"] = holehe_result or {"error": "Holehe search failed"}

    sherlock_found = combined["sherlock"].get("found_count", 0)
    holehe_found = combined["holehe"].get("found_count", 0)
    combined["found_count"] = sherlock_found + holehe_found
    combined["cross_validated"] = cross_validate_results(
        combined["sherlock"].get("account_checks", []),
        combined["holehe"].get("found", []),
    )
    combined["cross_validated_count"] = sum(
        1 for r in combined["cross_validated"] if r.get("cross_validated")
    )
    combined["search_links"] = [
        {"name": "Hunter.io", "url": f"https://hunter.io/search/{email}"},
        {"name": "EmailRep", "url": f"https://emailrep.io/{email}"},
        {
            "name": "Have I Been Pwned",
            "url": f"https://haveibeenpwned.com/unverifiedpwned?q={email}",
        },
        {"name": "Google", "url": f'https://www.google.com/search?q="{email}"'},
        {"name": "Dehashed", "url": f"https://dehashed.com/search?query={email}"},
    ]
    return combined


def lookup_email_combined(email):
    return asyncio.run(search_email_combined(email))


def calculate_confidence_score(result, source=None, cross_validated=False):
    score = 50
    if cross_validated:
        score += 30
    elif source == "holehe":
        score += 15
    elif source == "sherlock":
        score += 5
    if result.get("exists") == True:
        score += 15
    elif result.get("exists") == False:
        score -= 10
    http_status = result.get("http_status") or result.get("status_code")
    if http_status == 200:
        score += 10
    elif http_status and http_status != 200:
        score -= 5
    if result.get("rateLimit") or result.get("rate_limit"):
        score -= 20
    if result.get("emailrecovery"):
        score += 10
    if result.get("verification") == "verified":
        score += 15
    elif result.get("verification") == "likely_false":
        score -= 30
    return max(0, min(100, score))


def cross_validate_results(sherlock_results, holehe_results):
    if not sherlock_results and not holehe_results:
        return []
    sherlock_sites = {}
    holehe_sites = {}
    for r in sherlock_results or []:
        site_name = (r.get("site") or r.get("name") or "Unknown").lower()
        r["found_by"] = ["sherlock"]
        r["cross_validated"] = False
        r["confidence"] = calculate_confidence_score(r, "sherlock")
        sherlock_sites[site_name] = r
    for r in holehe_results or []:
        site_name = (r.get("site") or r.get("name") or "Unknown").lower()
        r["found_by"] = ["holehe"]
        r["cross_validated"] = False
        r["confidence"] = calculate_confidence_score(r, "holehe")
        holehe_sites[site_name] = r
    combined = []
    all_sites = set(sherlock_sites.keys()) | set(holehe_sites.keys())
    for site_name in all_sites:
        sherlock_r = sherlock_sites.get(site_name)
        holehe_r = holehe_sites.get(site_name)
        if sherlock_r and holehe_r:
            merged = {
                "site": site_name.title(),
                "exists": True,
                "found_by": ["sherlock", "holehe"],
                "cross_validated": True,
                "confidence": calculate_confidence_score(
                    sherlock_r, "both", cross_validated=True
                ),
                "sherlock_status": sherlock_r.get("http_status")
                or sherlock_r.get("status"),
                "holehe_status": "exists" if holehe_r.get("exists") else "not_found",
                "url": sherlock_r.get("url"),
                "emailrecovery": holehe_r.get("emailrecovery"),
                "rateLimit": sherlock_r.get("rateLimit") or holehe_r.get("rateLimit"),
            }
            combined.append(merged)
        elif sherlock_r:
            combined.append(
                {
                    "site": site_name.title(),
                    "exists": sherlock_r.get("exists"),
                    "found_by": ["sherlock"],
                    "cross_validated": False,
                    "confidence": calculate_confidence_score(sherlock_r, "sherlock"),
                    "sherlock_status": sherlock_r.get("http_status")
                    or sherlock_r.get("status"),
                    "url": sherlock_r.get("url"),
                    "rateLimit": sherlock_r.get("rateLimit"),
                }
            )
        elif holehe_r:
            combined.append(
                {
                    "site": site_name.title(),
                    "exists": holehe_r.get("exists"),
                    "found_by": ["holehe"],
                    "cross_validated": False,
                    "confidence": calculate_confidence_score(holehe_r, "holehe"),
                    "holehe_status": "exists"
                    if holehe_r.get("exists")
                    else "not_found",
                    "emailrecovery": holehe_r.get("emailrecovery"),
                    "rateLimit": holehe_r.get("rateLimit"),
                }
            )
    combined.sort(key=lambda x: (-x["cross_validated"], -x["confidence"]))
    return combined


__all__ = [
    "check_site_with_retry",
    "check_site_email",
    "check_email_site",
    "search_email_async",
    "lookup_email",
    "search_email_holehe",
    "lookup_email_holehe",
    "search_email_combined",
    "lookup_email_combined",
    "calculate_confidence_score",
    "cross_validate_results",
]
