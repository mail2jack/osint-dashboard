import asyncio
import logging
import re
from datetime import datetime

from curl_cffi import requests as curl_requests
from curl_cffi import CurlError

from cms.constants import HEADERS
from cms.validators import verify_profile, interpolate_string
from cms.search_tracking import get_maigret_database
from cms.sherlock_utils import get_sherlock_sites

logger = logging.getLogger(__name__)


async def check_username_async(client, platform, info, username):
    url = info["url"]
    finding = {
        "platform": platform,
        "url": url,
        "exists": None,
        "checked_at": datetime.now().isoformat(),
    }
    try:
        if info["type"] == "api" and "github" in url:
            response = await client.get(url, timeout=5, headers=HEADERS)
            finding["exists"] = response.status_code == 200
            if finding["exists"]:
                data = response.json()
                finding["details"] = {
                    "public_repos": data.get("public_repos", 0),
                    "followers": data.get("followers", 0),
                    "following": data.get("following", 0),
                    "name": data.get("name"),
                    "bio": data.get("bio"),
                }
                finding["verified"] = True
        elif info["type"] == "api":
            response = await client.get(url, timeout=5, headers=HEADERS)
            finding["exists"] = response.status_code == 200
            finding["verified"] = True
        else:
            response = await client.head(url, timeout=3, headers=HEADERS)
            finding["exists"] = response.status_code != 404
            if finding["exists"] and response.status_code == 200:
                verification = verify_profile("", username, url)
                finding["verification"] = verification
                if verification == "likely_false":
                    finding["exists"] = None
                    finding["status"] = "unverified"
    except CurlError as e:
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            finding["exists"] = "Timeout"
        elif "connection" in str(e).lower() or "couldn't connect" in str(e).lower():
            finding["exists"] = "Connection Error"
        else:
            finding["exists"] = "Unknown"
    except Exception:
        finding["exists"] = "Unknown"
    return finding


async def check_sherlock_site(client, site_name, site_info, username):
    finding = {
        "platform": site_name,
        "url": "",
        "exists": None,
        "status": "unknown",
        "http_status": None,
    }
    regex_check = site_info.get("regexCheck")
    if regex_check:
        try:
            if not re.search(regex_check, username):
                finding["status"] = "invalid_username"
                finding["exists"] = False
                return finding
        except Exception:
            logger.debug("Finding extraction failed for %s", site_info.get("url", "?"))

    url = interpolate_string(site_info.get("url", ""), username)
    finding["url"] = url
    request_method = site_info.get("request_method", "GET").upper()
    request_payload = site_info.get("request_payload", {})
    request_payload = interpolate_string(request_payload, username)
    headers = dict(HEADERS)
    if "headers" in site_info:
        headers.update(site_info["headers"])

    try:
        if request_method == "GET":
            response = await client.get(url, headers=headers, timeout=5)
        elif request_method == "HEAD":
            response = await client.head(url, headers=headers, timeout=10)
        elif request_method == "POST":
            response = await client.post(
                url,
                headers=headers,
                json=request_payload,
                timeout=10,
            )
        else:
            response = await client.get(url, headers=headers, timeout=5)

        finding["http_status"] = response.status_code
        response_text = response.text if hasattr(response, "text") else ""

        if "error" in site_info:
            if site_info["error"] in response_text:
                finding["status"] = "not_found"
                finding["exists"] = False
                finding["verified"] = True
            else:
                verification = verify_profile(response_text, username, url)
                finding["verification"] = verification
                if verification == "likely_false":
                    finding["status"] = "unverified"
                    finding["exists"] = None
                else:
                    finding["status"] = "found"
                    finding["exists"] = True
        elif "success" in site_info:
            if site_info["success"] in response_text:
                verification = verify_profile(response_text, username, url)
                finding["verification"] = verification
                if verification == "likely_false":
                    finding["status"] = "unverified"
                    finding["exists"] = None
                else:
                    finding["status"] = "found"
                    finding["exists"] = True
            else:
                finding["status"] = "not_found"
                finding["exists"] = False
                finding["verified"] = True
        else:
            if response.status_code == 200:
                verification = verify_profile(response_text, username, url)
                finding["verification"] = verification
                if verification == "likely_false":
                    finding["status"] = "unverified"
                    finding["exists"] = None
                elif verification == "verified":
                    finding["exists"] = True
                    finding["status"] = "found"
                else:
                    if (
                        "username" in site_info
                        or site_info.get("checkType") == "status"
                    ):
                        finding["exists"] = True
                        finding["status"] = "found"
                    else:
                        finding["exists"] = True
                        finding["status"] = "found"
            elif response.status_code == 404:
                finding["exists"] = False
                finding["status"] = "not_found"
                finding["verified"] = True
            else:
                finding["exists"] = response.status_code != 404
                finding["status"] = "unknown"

    except CurlError as e:
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            finding["status"] = "timeout"
        elif "connection" in str(e).lower() or "couldn't connect" in str(e).lower():
            finding["status"] = "connection_error"
        else:
            finding["status"] = "error"
        finding["exists"] = None
    except Exception:
        finding["status"] = "error"
        finding["exists"] = None

    return finding


async def search_username_async(username, progress_callback=None, max_sites=150):
    sherlock_sites = get_sherlock_sites()
    if not sherlock_sites:
        return {
            "username": username,
            "platforms_checked": 0,
            "findings": [],
            "error": "Could not load Sherlock site data",
        }

    sites_list = list(sherlock_sites.items())[:max_sites]
    all_findings = []
    total_sites = len(sites_list)
    checked = 0
    found_count = 0
    batch_size = 30

    async with curl_requests.AsyncSession(
        impersonate="chrome124", timeout=30
    ) as client:
        for i in range(0, total_sites, batch_size):
            batch = sites_list[i : i + batch_size]
            tasks = []
            for site_name, site_info in batch:
                tasks.append(
                    check_sherlock_site(client, site_name, site_info, username)
                )

            for site_name, site_info, task in zip(
                [s[0] for s in batch], [s[1] for s in batch], tasks
            ):
                try:
                    result = await asyncio.wait_for(task, timeout=10)
                    all_findings.append(result)
                    if result.get("exists") == True:
                        found_count += 1
                except (asyncio.TimeoutError, Exception):
                    all_findings.append(
                        {"site": site_name, "exists": False, "status": "error"}
                    )
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

    result = {
        "username": username,
        "platforms_checked": total_sites,
        "findings": all_findings,
    }
    result["found_count"] = sum(1 for f in all_findings if f.get("exists") == True)
    result["not_found_count"] = sum(1 for f in all_findings if f.get("exists") == False)
    result["invalid_count"] = sum(
        1 for f in all_findings if f.get("status") == "invalid_username"
    )
    result["error_count"] = sum(
        1
        for f in all_findings
        if f.get("status") in ["timeout", "connection_error", "error", "unknown"]
    )
    return result


def search_username(username):
    return asyncio.run(search_username_async(username))


def search_username_maigret(username, progress_callback=None, max_sites=500):
    try:
        import maigret.maigret as maigret_module
        import logging as _logging

        db = get_maigret_database()
        if not db:
            return {
                "username": username,
                "platforms_checked": 0,
                "findings": [],
                "error": "Could not load Maigret database",
            }

        maigret_logger = _logging.getLogger("maigret")
        maigret_logger.setLevel(_logging.WARNING)

        sites_list = sorted(
            db.sites,
            key=lambda x: getattr(x, "rank", 9999) if hasattr(x, "rank") else 9999,
        )
        limited_sites = sites_list[:max_sites]
        limited_dict = {site.name: site for site in limited_sites}

        class ProgressNotifier:
            def __init__(self, callback, total):
                self.callback = callback
                self.checked = 0
                self.total = total

            def update(self, checked, total, found=None):
                self.checked = checked
                if self.callback:
                    self.callback(
                        {
                            "checked": checked,
                            "total": total,
                            "found": found if found else 0,
                            "percent": int((checked / total) * 100) if total > 0 else 0,
                            "current_site": "maigret",
                        }
                    )

        notifier = (
            ProgressNotifier(progress_callback, len(limited_dict))
            if progress_callback
            else None
        )

        results = asyncio.run(
            maigret_module.maigret(
                username=username,
                site_dict=limited_dict,
                logger=maigret_logger,
                query_notify=notifier,
                timeout=2,
                is_parsing_enabled=False,
                max_connections=30,
                no_progressbar=True,
            )
        )

        findings = []
        found_count = 0

        for site_name, site_result in results.items():
            exists = site_result.get("exists", False)
            status = site_result.get("status", "unknown")
            finding = {
                "site": site_name,
                "url": site_result.get("url_user") or site_result.get("url_main", ""),
                "exists": exists,
                "status": status,
                "http_status": site_result.get("http_status"),
                "rank": site_result.get("rank"),
            }
            if exists:
                found_count += 1
            findings.append(finding)

        return {
            "username": username,
            "platforms_checked": len(findings),
            "findings": findings,
            "found_count": found_count,
            "method": "maigret",
            "total_sites_available": len(db.sites),
        }

    except Exception:
        logger.exception("Maigret username search failed")
        return {
            "username": username,
            "platforms_checked": 0,
            "findings": [],
            "error": "Search failed",
        }


__all__ = [
    "check_username_async",
    "check_sherlock_site",
    "search_username_async",
    "search_username",
    "search_username_maigret",
]
