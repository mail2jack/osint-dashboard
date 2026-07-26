import json
import logging
import re

from cms.workflow.actions.helpers import _first_subject, _get_api_key
from cms.workflow.actions.company_action import _kvk_check

logger = logging.getLogger(__name__)

_JUNK_DOMAINS = frozenset(
    [
        "osintteam.blog",
        "boxpiper.com",
        "dorksearch.com",
        "google.com",
        "bing.com",
        "duckduckgo.com",
        "search.brave.com",
        "reddit.com/r/",
        "medium.com/@",
        "youtube.com/watch",
        "github.com/peaceiris",
        "slideshare.net",
    ]
)


def _strip_dork_syntax(query):
    """Strip Google dork operators to get plain search terms for Brave/DDG.

    Google-only operators like site:, intext:, filetype: are removed.
    Useful quoted terms and keywords are kept as a plain query.
    """
    q = query
    q = re.sub(r"-?site:\S+", "", q)
    q = re.sub(r'-?intext:"([^"]*)"', r'"\1"', q)
    q = re.sub(r'-?intitle:"([^"]*)"', r'"\1"', q)
    q = re.sub(r'-?inurl:"([^"]*)"', r'"\1"', q)
    q = re.sub(r"-?intext:\S+", "", q)
    q = re.sub(r"-?intitle:\S+", "", q)
    q = re.sub(r"-?inurl:\S+", "", q)
    q = re.sub(r"-?filetype:\S+", "", q)
    q = re.sub(r"after:\S+", "", q)
    q = re.sub(r"before:\S+", "", q)
    q = re.sub(r"\bOR\b", " ", q)
    q = q.replace("(", "").replace(")", "")
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _is_junk_url(url):
    """Check if a URL is from a low-quality / irrelevant domain."""
    if not url:
        return True
    url_lower = url.lower()
    for d in _JUNK_DOMAINS:
        if d in url_lower:
            return True
    return False


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


def _process_dork_results(raw_results, dork_label, dork_id, query, subject_id):
    """Process raw search results into findings, deduplicating by URL."""
    findings = []
    seen_urls = set()

    def _add_finding(r):
        url = r.get("url", "")
        if not url or url in seen_urls or _is_junk_url(url):
            return
        seen_urls.add(url)
        title = f"{r.get('title', '')[:200] or url}"
        detail = r.get("description", "")[:300] or url
        if dork_label:
            title = f"[{dork_label}] {title}"
            dork_info = f"Dork: {dork_label}"
            if dork_id:
                dork_info += f" ({dork_id})"
            detail = f"{dork_info}\nQuery: {query}\n{detail}"
        findings.append(
            {
                "title": title,
                "detail": detail,
                "source_url": url,
                "source_type": "google_dork",
                "icon": "🔎",
                "verified": False,
                "subject_id": subject_id,
                "screenshots": [{"url": None, "source_url": url}],
            }
        )

    for r in raw_results:
        _add_finding(r)
    return findings, seen_urls


def _execute_dork_queries(
    plain_query, subject, action, dork_label, dork_id, subject_id
):
    """Run Brave then DDG dork searches, returning findings and seen URLs."""
    from cms.services.search_service import brave_search, ddg_single_query

    all_raw = []

    brave_key = _get_api_key("brave_api_key")
    if plain_query and brave_key:
        try:
            all_raw.extend(brave_search(plain_query, api_key=brave_key)[:10])
        except Exception as e:
            logger.debug("Brave dork search failed: %s", e)

    findings, seen_urls = _process_dork_results(
        all_raw, dork_label, dork_id, query=plain_query, subject_id=subject_id
    )

    if plain_query and not findings:
        try:
            ddg_raw = ddg_single_query(plain_query, max_results=10)
            ddg_findings, ddg_seen = _process_dork_results(
                ddg_raw, dork_label, dork_id, query=plain_query, subject_id=subject_id
            )
            findings.extend(ddg_findings)
            seen_urls.update(ddg_seen)
        except Exception as e:
            logger.debug("DDG dork search failed: %s", e)

    if dork_id.startswith("company-kvk") and subject:
        try:
            kvk_findings = _kvk_check(action)
            for f in kvk_findings:
                key = f.get("source_url", "") or f.get("title", "")
                if key not in seen_urls:
                    seen_urls.add(key)
                    findings.append(f)
        except Exception as e:
            logger.debug("KvK direct lookup from dork failed: %s", e)

    return findings, seen_urls


def _google_dork_search(action):
    """Execute a user-constructed Google dork query.

    Priority: Brave (best quality) → DDG (free fallback) → direct lookups.
    """
    raw_value = action.data_value if action.data_value else None
    subject = _first_subject(action)
    subject_id = subject.id if subject else None
    if not raw_value:
        return []

    dork_label = ""
    dork_id = ""
    query = raw_value
    try:
        payload = json.loads(raw_value)
        if isinstance(payload, dict) and "query" in payload:
            query = payload["query"]
            dork_label = payload.get("dork_label", "")
            dork_id = payload.get("dork_id", "")
    except (json.JSONDecodeError, TypeError):
        pass

    plain_query = _strip_dork_syntax(query)
    findings, _ = _execute_dork_queries(
        plain_query, subject, action, dork_label, dork_id, subject_id
    )

    if not findings:
        no_result_detail = f"The query returned no results: {query}"
        if dork_label:
            no_result_detail = f"Dork: {dork_label}\nQuery: {query}\nNo results found."
        findings.append(
            {
                "title": f"No results — {dork_label}"
                if dork_label
                else "No dork results found",
                "detail": no_result_detail,
                "source_type": "google_dork",
                "icon": "🔎",
                "verified": False,
                "subject_id": subject_id,
            }
        )

    return findings
