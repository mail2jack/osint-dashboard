import logging
import re
import time
import os
import threading
import httpx
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)
_dorks_log_lock = threading.Lock()

_TOR_ENABLED: bool | None = None
_TOR_PROXY: str | None = None
_TOR_LAST_CHECK: float = 0


def _refresh_tor_config() -> None:
    """Refresh Tor config cache from Settings (cached 60s)."""
    global _TOR_ENABLED, _TOR_PROXY, _TOR_LAST_CHECK
    now = time.time()
    if now - _TOR_LAST_CHECK < 60:
        return
    _TOR_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("tor_enabled", "false")
            _TOR_ENABLED = val.lower() in ("true", "1", "yes")
            _TOR_PROXY = Setting.get("tor_proxy", "socks5://127.0.0.1:9050")
    except Exception:
        _TOR_ENABLED = os.environ.get("TOR_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        _TOR_PROXY = os.environ.get("TOR_PROXY", "socks5://127.0.0.1:9050")


def _get_http_client(
    timeout: float = 10.0, headers: dict | None = None, follow_redirects: bool = True
) -> httpx.Client:
    """Return httpx.Client — routes through Tor proxy when enabled."""
    _refresh_tor_config()
    kwargs: dict = {"timeout": timeout, "follow_redirects": follow_redirects}
    if headers:
        kwargs["headers"] = headers
    if _TOR_ENABLED and _TOR_PROXY:
        kwargs["proxy"] = _TOR_PROXY
    return httpx.Client(**kwargs)


def search_person(full_name):
    """Person search using Brave API / DuckDuckGo via person_dorks_search."""
    return person_dorks_search(full_name)


def brave_search(query, api_key) -> list:
    """Search using Brave Search API.

    Returns list of results or empty list if failed.
    Requires BRAVE_API_KEY environment variable.
    """
    if not api_key:
        return []

    try:
        headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}

        url = "https://api.search.brave.com/res/v1/web/search"
        params = {"q": query, "count": 10}

        with _get_http_client(timeout=8.0, headers=headers) as client:
            response = client.get(url, params=params)

        if response.status_code != 200:
            return []

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

    except httpx.TimeoutException:
        logger.debug("Brave search timeout")
        return []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("Brave search rate limited (429)")
        else:
            logger.debug(f"Brave search HTTP {e.response.status_code}")
        return []
    except Exception as e:
        logger.debug(f"Brave search error ({type(e).__name__}): {e}")
        return []


def _get_brave_key() -> str:
    """Get Brave API key: env var first, then DB Setting as fallback."""
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        try:
            from flask import current_app

            with current_app.app_context():
                from cms.models import Setting

                key = Setting.get("brave_api_key", "")
        except Exception as e:
            logger.debug(f"_get_brave_key failed ({type(e).__name__}): {e}")
    return key


def person_dorks_search(full_name) -> dict:
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
    if brave_api_key:
        logger.info("Using Brave Search API")
        results["sources_used"].append("brave")

        log_ddg("Using Brave Search API (key configured)")

        for query in dork_queries[:6]:
            results["queries_run"].append(query)
            try:
                brave_results = brave_search(query, brave_api_key)
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
    else:
        log_ddg("Brave API key not configured - skipping Brave search")

    ddg_success = False
    if not brave_success or not results["dorks_results"]:
        logger.info("Trying DuckDuckGo scraping methods")
        log_ddg("Trying DuckDuckGo scraping...")

        ddg_methods = [
            {"name": "duckduckgo_lite", "url": "https://lite.duckduckgo.com/50x.html"},
            {"name": "duckduckgo_html", "url": "https://html.duckduckgo.com/html/"},
        ]

        for method in ddg_methods:
            if ddg_success and results["ddg_results_count"] > 5:
                break
            if results["brave_results_count"] > 5:
                break

            try:
                client = _get_http_client(
                    timeout=6.0,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Connection": "keep-alive",
                    },
                )

                for query in dork_queries[:5]:
                    if ddg_success and results["ddg_results_count"] > 5:
                        break

                    results["queries_run"].append(query)
                    method_url = method["url"]
                    params = {"q": query}

                    try:
                        response = client.get(method_url, params=params)
                        log_ddg(f"DDG Query: {query}")
                        log_ddg(f"  Status: {response.status_code}")

                        if response.status_code == 200 and response.text:
                            found_count = 0

                            if "duckduckgo_lite" in method["name"]:
                                links = re.findall(
                                    r'<a rel="nofollow" href="(https?://[^"]+)"',
                                    response.text,
                                )
                                for link in links[:10]:
                                    add_result(link, query, "duckduckgo")
                                    found_count += 1

                            elif "duckduckgo_html" in method["name"]:
                                redirect_links = re.findall(
                                    r'uddg=(https?%3A%2F%2F[^&"]+)', response.text
                                )
                                for link in redirect_links[:10]:
                                    add_result(
                                        unquote(unquote(link)), query, "duckduckgo"
                                    )
                                    found_count += 1

                            if found_count > 0:
                                ddg_success = True
                                if "duckduckgo" not in results["sources_used"]:
                                    results["sources_used"].append("duckduckgo")

                    except httpx.TimeoutException:
                        log_ddg("  Timeout")
                        continue
                    except httpx.HTTPStatusError as e:
                        log_ddg(f"  HTTP {e.response.status_code}")
                        continue
                    except Exception as e:
                        log_ddg(f"  Exception ({type(e).__name__}): {str(e)}")
                        continue

                    time.sleep(0.3)

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
