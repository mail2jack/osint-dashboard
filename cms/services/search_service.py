import logging
import re
import time
import os
import threading
from urllib.parse import quote, unquote

from curl_cffi import requests as curl_requests
from curl_cffi import CurlError

from cms.services.http_utils import jitter_sleep, get_next_proxy, next_impersonate

logger = logging.getLogger(__name__)
_dorks_log_lock = threading.Lock()


def _get_http_client(
    timeout: float = 10.0, headers: dict | None = None
) -> curl_requests.Session:
    """Return curl_cffi.Session with rotating browser TLS fingerprint impersonation.

    Routes through Tor (prioriteit) of proxy rotation via http_utils.get_next_proxy().
    """
    kwargs: dict = {
        "timeout": timeout,
        "impersonate": next_impersonate(),
    }
    if headers:
        kwargs["headers"] = headers
    proxies = get_next_proxy()
    if proxies:
        kwargs["proxies"] = proxies
    return curl_requests.Session(**kwargs)


def search_person(full_name):
    """Person search using Brave API / DuckDuckGo via person_dorks_search."""
    return person_dorks_search(full_name)


def brave_search(query, api_key, results_meta: dict | None = None) -> list:
    """Search using Brave Search API.

    Returns list of results or empty list if failed.
    Populates results_meta with 'brave_status' on non-200.
    Brave API uses direct HTTPS (no Tor) since it uses an API key for auth.
    """
    if not api_key:
        return []

    try:
        headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}

        url = "https://api.search.brave.com/res/v1/web/search"
        params = {"q": query, "count": 10}

        with curl_requests.Session(
            timeout=12.0, impersonate=next_impersonate(), headers=headers
        ) as client:
            jitter_sleep(domain_hint=url)
            response = client.get(url, params=params)

        if response.status_code != 200:
            if results_meta is not None:
                results_meta["brave_status"] = response.status_code
            return []

        if results_meta is not None:
            remaining_header = response.headers.get("X-RateLimit-Remaining", "")
            limit_header = response.headers.get("X-RateLimit-Limit", "")
            try:
                parts_remaining = remaining_header.split(",")
                parts_limit = limit_header.split(",")
                if len(parts_remaining) >= 2 and len(parts_limit) >= 2:
                    results_meta["brave_remaining_monthly"] = int(
                        parts_remaining[1].strip()
                    )
                    results_meta["brave_limit_monthly"] = int(parts_limit[1].strip())
            except (ValueError, IndexError):
                pass

        data = response.json()
        results = []

        web_results = data.get("web", {}).get("results", [])
        for item in web_results:
            results.append(
                {
                    "url": item.get("url", ""),
                    "domain": item.get("domain", ""),
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                }
            )

        return results

    except CurlError as e:
        if "timed out" in str(e).lower():
            logger.debug("Brave search timeout")
        elif "429" in str(e):
            logger.warning("Brave search rate limited")
        else:
            logger.debug(f"Brave search CurlError: {e}")
        return []
    except Exception as e:
        logger.debug(f"Brave search error ({type(e).__name__}): {e}")
        return []


def _get_brave_key() -> str:
    """Get Brave API key: DB Setting first, then env var as fallback."""
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            key = Setting.get("brave_api_key", "")
            if key:
                return key
    except Exception as e:
        logger.debug("_get_brave_key (Setting) failed: %s", e)
    return os.environ.get("BRAVE_API_KEY", "")


_DDG_UA_BASE = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36"
_DDG_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "DNT": "1",
}


def _ddg_is_blocked(text):
    return any(
        p in text.lower()
        for p in [
            "challenge-platform",
            "cf-browser-request",
            "please complete the security",
            "captcha",
            "ddos",
            "blocked",
            "automated requests",
        ]
    )


def ddg_single_query(query, max_results=10):
    """Run a single query against DuckDuckGo (lite -> html -> api cascade).

    Returns list of {"url": ..., "title": ..., "description": ...} dicts.
    Uses curl_cffi with TLS fingerprint rotation and jitter for OPSEC.
    """
    results = []
    seen = set()

    ddg_methods = [
        {
            "name": "lite",
            "url": "https://lite.duckduckgo.com/lite/",
            "ua_chrome": "121",
        },
        {
            "name": "html",
            "url": "https://html.duckduckgo.com/html/",
            "ua_chrome": "122",
        },
        {"name": "api", "url": "https://api.duckduckgo.com/", "ua_chrome": "123"},
    ]

    for method in ddg_methods:
        if len(results) >= max_results:
            break
        try:
            headers = dict(_DDG_HEADERS)
            headers["User-Agent"] = _DDG_UA_BASE.format(method["ua_chrome"])
            client = _get_http_client(timeout=7.0, headers=headers)

            params = {"q": query}
            if method["name"] == "api":
                params.update({"format": "json", "no_html": "1", "skip_disambig": "1"})

            jitter_sleep(domain_hint=method["url"])
            response = client.get(method["url"], params=params)

            if response.status_code != 200 or not response.text:
                client.close()
                continue

            text = response.text
            if _ddg_is_blocked(text):
                client.close()
                break

            found = 0

            if method["name"] == "lite":
                for pat in [
                    r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"',
                    r'<a[^>]+href="(https?://[^"]+)"[^>]*>',
                ]:
                    links = re.findall(pat, text)
                    if links:
                        for link in links[:10]:
                            if link not in seen:
                                seen.add(link)
                                results.append(
                                    {"url": link, "title": "", "description": ""}
                                )
                                found += 1
                        break

            elif method["name"] == "html":
                redirect_links = re.findall(r'uddg=(https?%3A%2F%2F[^&"]+)', text)
                if redirect_links:
                    for link in redirect_links[:10]:
                        decoded = unquote(unquote(link))
                        if decoded not in seen:
                            seen.add(decoded)
                            results.append(
                                {"url": decoded, "title": "", "description": ""}
                            )
                            found += 1
                else:
                    links = re.findall(
                        r'<a[^>]+href="(https?://[^"]+)"[^>]*class="result__a"', text
                    )
                    if not links:
                        links = re.findall(
                            r'<a[^>]+href="(https?://[^"]+)"[^>]*rel="nofollow"', text
                        )
                    for link in links[:10]:
                        if link not in seen:
                            seen.add(link)
                            results.append(
                                {"url": link, "title": "", "description": ""}
                            )
                            found += 1

            elif method["name"] == "api":
                try:
                    import json as _json

                    api_data = _json.loads(text)
                    for topic in api_data.get("RelatedTopics", []):
                        if "Topics" in topic:
                            for sub in topic["Topics"]:
                                url = sub.get("FirstURL") or sub.get("URL", "")
                                if url and url not in seen:
                                    seen.add(url)
                                    results.append(
                                        {
                                            "url": url,
                                            "title": sub.get("Text", "")[:200],
                                            "description": sub.get("Text", "")[:300],
                                        }
                                    )
                                    found += 1
                        else:
                            url = topic.get("FirstURL") or topic.get("URL", "")
                            if url and url not in seen:
                                seen.add(url)
                                results.append(
                                    {
                                        "url": url,
                                        "title": topic.get("Text", "")[:200],
                                        "description": topic.get("Text", "")[:300],
                                    }
                                )
                                found += 1
                    for res in api_data.get("Results", []):
                        url = res.get("FirstURL") or res.get("URL", "")
                        if url and url not in seen:
                            seen.add(url)
                            results.append(
                                {
                                    "url": url,
                                    "title": res.get("Text", "")[:200],
                                    "description": res.get("Text", "")[:300],
                                }
                            )
                            found += 1
                except Exception as e:
                    logger.debug("DDG API parse error: %s", e)

            client.close()
            if found > 0 and len(results) >= max_results:
                break
            time.sleep(0.5)

        except CurlError:
            continue
        except Exception:
            continue

    return results[:max_results]


def person_dorks_search(full_name, cancel_event: threading.Event | None = None) -> dict:
    """Search using Google dorks to find person info across web.

    Uses Brave Search API if available, falls back to multiple DuckDuckGo methods.
    Tracks source for each result and shows which source was used.
    """
    from datetime import datetime

    parts = full_name.strip().split()
    if len(parts) < 2:
        return {"error": "Please enter first and last name", "results": None}

    first_name = parts[0]
    last_name = " ".join(parts[1:])

    logger.info(f"Dorks search started for: {full_name}")

    dorks_log_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "dorks_log.txt"
    )
    log_start = f"\n=== {datetime.now()} - Dorks search: {full_name} ===\n"
    try:
        with _dorks_log_lock, open(dorks_log_file, "a") as f:
            f.write(log_start)
    except Exception:
        logger.warning("Failed to write search log")

    search_query = quote(f'"{first_name}" "{last_name}"')
    search_links = [
        {
            "engine": "Google",
            "name": "Search on Google",
            "url": f"https://www.google.com/search?q={search_query}",
            "query": f'"{first_name}" "{last_name}"',
        },
        {
            "engine": "LinkedIn",
            "name": "Search on LinkedIn",
            "url": f"https://www.linkedin.com/search/results/all/?keywords={quote(first_name + ' ' + last_name)}",
            "query": "LinkedIn Profile",
        },
        {
            "engine": "Facebook",
            "name": "Search on Facebook",
            "url": f"https://www.facebook.com/search/top?q={quote(first_name + ' ' + last_name)}",
            "query": "Facebook Profile",
        },
        {
            "engine": "Twitter/X",
            "name": "Search on Twitter/X",
            "url": f"https://nitter.net/search?f=users&q={quote(first_name + ' ' + last_name)}",
            "query": "Twitter Profile",
        },
        {
            "engine": "GitHub",
            "name": "Search on GitHub",
            "url": f"https://github.com/search?q={quote(first_name + '+' + last_name)}&type=users",
            "query": "GitHub Profile",
        },
        {
            "engine": "Instagram",
            "name": "Search on Instagram",
            "url": f"https://www.instagram.com/{quote(first_name + last_name)}/",
            "query": "Instagram Profile",
        },
        {
            "engine": "Reddit",
            "name": "Search on Reddit",
            "url": f"https://www.reddit.com/search/?q={quote(first_name + ' ' + last_name)}",
            "query": "Reddit Posts",
        },
        {
            "engine": "YouTube",
            "name": "Search on YouTube",
            "url": f"https://www.youtube.com/results?search_query={quote(first_name + ' ' + last_name)}",
            "query": "YouTube Channel",
        },
        {
            "engine": "TikTok",
            "name": "Search on TikTok",
            "url": f"https://www.tiktok.com/@{quote(first_name + last_name)}",
            "query": "TikTok Profile",
        },
        {
            "engine": "Pipl",
            "name": "Search on Pipl",
            "url": f"https://pipl.com/search/?q={search_query}",
            "query": "Deep Web Search",
        },
    ]

    dork_queries = [
        f'"{first_name} {last_name}" profile',
        f'"{full_name}" site:linkedin.com',
        f'"{full_name}" site:facebook.com',
        f'"{full_name}" site:twitter.com OR site:x.com',
        f'"{full_name}" site:instagram.com',
        f'"{full_name}" site:tiktok.com',
        f'"{full_name}" site:youtube.com',
        f'"{full_name}" site:github.com',
        f'"{full_name}" site:reddit.com',
        f'"{full_name}" filetype:pdf',
        f'"{full_name}" filetype:doc OR filetype:docx',
        f'"{full_name}" email',
    ]

    results = {
        "name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "search_links": search_links,
        "dorks_results": [],
        "total_results": 0,
        "queries_run": [],
        "sources_used": [],
        "brave_results_count": 0,
        "ddg_results_count": 0,
    }

    seen = set()
    exclude_domains = [
        "duckduckgo.com",
        "bing.com",
        "google.com",
        "microsoft.com",
        "yahoo.com",
        "duck.com",
        "brave.com",
        "duckduckgo",
        "lite.duckduckgo",
    ]

    def get_category(domain):
        if any(
            s in domain
            for s in [
                "linkedin",
                "facebook",
                "twitter",
                "instagram",
                "tiktok",
                "youtube",
                "mastodon",
            ]
        ):
            return "social_media"
        elif any(s in domain for s in ["pdf", "doc", "docx", "xls", "xlsx", "csv"]):
            return "files"
        elif any(
            s in domain for s in ["news", "medium", "blog", "wordpress", "substack"]
        ):
            return "news"
        elif any(
            s in domain
            for s in [
                "whitepages",
                "truecaller",
                "spokeo",
                "pipl",
                "fastbackgroundcheck",
            ]
        ):
            return "people_search"
        return "general"

    def add_result(link, query, source="unknown"):
        try:
            if not link or "://" not in link:
                return
            domain = re.sub(r"https?://(www\.)?", "", link).split("/")[0]
            if (
                domain
                and domain not in seen
                and not any(ex in domain for ex in exclude_domains)
            ):
                seen.add(domain)
                category = get_category(domain)

                results["dorks_results"].append(
                    {
                        "url": link,
                        "domain": domain,
                        "query": query[:60] if query else "",
                        "category": category,
                        "source": source,
                    }
                )
                results["total_results"] += 1

                if source == "brave":
                    results["brave_results_count"] += 1
                elif source == "duckduckgo":
                    results["ddg_results_count"] += 1
        except Exception:
            logger.debug("Failed to parse search result")

    brave_success = False

    def log_ddg(msg):
        try:
            with _dorks_log_lock, open(dorks_log_file, "a") as f:
                f.write(msg + "\n")
                f.flush()
        except Exception:
            logger.warning("Failed to flush search log")

    brave_api_key = _get_brave_key()
    brave_meta: dict = {}
    if brave_api_key:
        logger.info("Using Brave Search API")
        results["sources_used"].append("brave")

        log_ddg("Using Brave Search API (key configured)")

        for query in dork_queries[:6]:
            if cancel_event and cancel_event.is_set():
                log_ddg("  Cancelled via cancel_event")
                break
            results["queries_run"].append(query)
            try:
                brave_results = brave_search(query, brave_api_key, brave_meta)
                log_ddg(f"Brave Query: {query}")
                log_ddg(f"  Brave found {len(brave_results)} results")
                if brave_results:
                    brave_success = True
                    for item in brave_results:
                        add_result(item.get("url", ""), query, "brave")
                time.sleep(0.15)
            except Exception as e:
                log_ddg(f"  Brave error: {str(e)}")
                logger.warning(f"Brave search error: {e}")

        bs = brave_meta.get("brave_status", 0)
        remaining = brave_meta.get("brave_remaining_monthly")
        limit = brave_meta.get("brave_limit_monthly")
        if remaining is not None and limit is not None:
            used = limit - remaining
            est_cost = 5.0 + (used / 1000) * 5.0
            results["brave_usage"] = {
                "remaining": remaining,
                "limit": limit,
                "used": used,
                "estimated_cost": round(est_cost, 2),
            }
            pct = remaining / limit * 100 if limit else 0
            if pct < 20:
                logger.warning(
                    f"Brave API quota low: {remaining}/{limit} ({pct:.0f}%) — est. cost ${est_cost:.2f} this month"
                )
                results["brave_warning"] = (
                    f"Brave quota bijna op: {remaining}/{limit} ({pct:.0f}%) — ~${est_cost:.2f} deze maand"
                )

        if bs == 402:
            results["brave_error"] = "Brave API quota exhausted (402)"
        elif bs:
            results["brave_error"] = f"Brave API returned HTTP {bs}"
    else:
        log_ddg("Brave API key not configured - skipping Brave search")

    ddg_success = False
    if not brave_success or not results["dorks_results"]:
        logger.info("Trying DuckDuckGo scraping methods")
        log_ddg("Trying DuckDuckGo scraping...")

        _ua_base = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36"
        _ddg_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "DNT": "1",
        }

        def _is_blocked(text):
            return any(
                p in text.lower()
                for p in [
                    "challenge-platform",
                    "cf-browser-request",
                    "please complete the security",
                    "captcha",
                    "ddos",
                    "blocked",
                    "automated requests",
                ]
            )

        ddg_methods = [
            # 1) DDG Lite — simplest HTML, most stable endpoint
            {
                "name": "lite",
                "url": "https://lite.duckduckgo.com/lite/",
                "ua_chrome": "121",
            },
            # 2) DDG HTML — full results, uses redirect wrapping
            {
                "name": "html",
                "url": "https://html.duckduckgo.com/html/",
                "ua_chrome": "122",
            },
            # 3) DDG JSON API — no scraping, limited results but very stable
            {"name": "api", "url": "https://api.duckduckgo.com/", "ua_chrome": "123"},
        ]

        for method in ddg_methods:
            if ddg_success and results["ddg_results_count"] > 5:
                break
            if results["brave_results_count"] > 5:
                break

            try:
                headers = dict(_ddg_headers)
                headers["User-Agent"] = _ua_base.format(method["ua_chrome"])
                client = _get_http_client(timeout=7.0, headers=headers)

                queries_to_run = (
                    dork_queries[:5] if method["name"] != "api" else dork_queries[:3]
                )
                for query in queries_to_run:
                    if ddg_success and results["ddg_results_count"] > 5:
                        break

                    results["queries_run"].append(query)
                    method_url = method["url"]
                    params = {"q": query}
                    if method["name"] == "api":
                        params.update(
                            {"format": "json", "no_html": "1", "skip_disambig": "1"}
                        )

                    try:
                        jitter_sleep(domain_hint=method_url)
                        response = client.get(method_url, params=params)

                        log_ddg(f"DDG {method['name']}: {query}")
                        log_ddg(f"  Status: {response.status_code}")

                        if response.status_code == 200 and response.text:
                            text = response.text
                            if _is_blocked(text):
                                log_ddg("  BLOCKED — skipping method")
                                break

                            found_count = 0

                            if method["name"] == "lite":
                                for pat in [
                                    r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"',
                                    r'<a[^>]+href="(https?://[^"]+)"[^>]*>',
                                ]:
                                    links = re.findall(pat, text)
                                    if links:
                                        for link in links[:10]:
                                            add_result(link, query, "duckduckgo")
                                            found_count += 1
                                        break

                            elif method["name"] == "html":
                                redirect_links = re.findall(
                                    r'uddg=(https?%3A%2F%2F[^&"]+)', text
                                )
                                if redirect_links:
                                    for link in redirect_links[:10]:
                                        add_result(
                                            unquote(unquote(link)), query, "duckduckgo"
                                        )
                                        found_count += 1
                                else:
                                    links = re.findall(
                                        r'<a[^>]+href="(https?://[^"]+)"[^>]*class="result__a"',
                                        text,
                                    )
                                    if not links:
                                        links = re.findall(
                                            r'<a[^>]+href="(https?://[^"]+)"[^>]*rel="nofollow"',
                                            text,
                                        )
                                    for link in links[:10]:
                                        add_result(link, query, "duckduckgo")
                                        found_count += 1

                            elif method["name"] == "api":
                                try:
                                    import json as _json

                                    api_data = _json.loads(text)
                                    seen_api = set()
                                    for topic in api_data.get("RelatedTopics", []):
                                        if "Topics" in topic:
                                            for sub in topic["Topics"]:
                                                url = sub.get("FirstURL") or sub.get(
                                                    "URL", ""
                                                )
                                                if url and url not in seen_api:
                                                    seen_api.add(url)
                                                    add_result(url, query, "duckduckgo")
                                                    found_count += 1
                                        else:
                                            url = topic.get("FirstURL") or topic.get(
                                                "URL", ""
                                            )
                                            if url and url not in seen_api:
                                                seen_api.add(url)
                                                add_result(url, query, "duckduckgo")
                                                found_count += 1
                                    for res in api_data.get("Results", []):
                                        url = res.get("FirstURL") or res.get("URL", "")
                                        if url and url not in seen_api:
                                            seen_api.add(url)
                                            add_result(url, query, "duckduckgo")
                                            found_count += 1
                                except Exception as e:
                                    log_ddg(f"  API parse error: {e}")

                            if found_count > 0:
                                ddg_success = True
                                if "duckduckgo" not in results["sources_used"]:
                                    results["sources_used"].append("duckduckgo")
                            log_ddg(f"  Found {found_count} results")

                    except CurlError as e:
                        log_ddg(f"  CurlError: {str(e)[:80]}")
                        continue
                    except Exception as e:
                        log_ddg(f"  Exception ({type(e).__name__}): {str(e)}")
                        continue

                    time.sleep(0.5)

                client.close()

            except Exception as e:
                log_ddg(f"  Method error ({type(e).__name__}): {str(e)}")
                continue

    if results["brave_results_count"] > 0:
        results["sources_used"].append("brave")
    if results["ddg_results_count"] > 0:
        results["sources_used"].append("duckduckgo")

    results["source_summary"] = {
        "brave": f"Brave Search ({results['brave_results_count']} results)",
        "duckduckgo": f"DuckDuckGo ({results['ddg_results_count']} results)",
    }

    logger.info(
        f"Search complete: {results['total_results']} results from {results['sources_used']}"
    )

    log_ddg(
        f"=== COMPLETE: {results['total_results']} dork results, {len(results['search_links'])} search links ==="
    )

    return results
