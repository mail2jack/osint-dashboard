import json
import logging
import re

from cms.services.http_utils import jittered_get
from cms.workflow.actions.helpers import (
    _action_subject,
    _get_api_key,
    _site_dork_search,
)
from cms.workflow.actions.registry import _use_credit, _has_credits

logger = logging.getLogger(__name__)

PLATFORM_API_URLS = {
    "facebook_pullapi": "https://facebook-scraper-api9.p.rapidapi.com/facebook/profile",
    "facebook_scraper3": "https://facebook-scraper3.p.rapidapi.com",
    "tiktok_user": "https://scraptik.p.rapidapi.com/get-user",
    "tiktok_search": "https://scraptik.p.rapidapi.com/search-users",
    "instagram_userinfo": "https://pro-social.p.rapidapi.com/userinfo_username/",
    "instagram_search": "https://pro-social.p.rapidapi.com/usersearch/",
    "linkedin_base": "https://linkedin-data-api.p.rapidapi.com",
    "twitter_screenname": "https://twitter-api45.p.rapidapi.com/screenname.php",
    "twitter_search": "https://twitter-api45.p.rapidapi.com/search.php",
}


# ─── Shared helpers ─────────────────────────────────────────────


def _resolve_query(action):
    query = action.data_value if action.data_value else None
    subject = _action_subject(action)
    if not query:
        query = subject.name if subject else ""
    if not query:
        return None
    subject_id = subject.id if subject else None
    name_for_dork = subject.name if subject else query
    return query, subject_id, name_for_dork


def _run_dork_search(domain, name_for_dork, subject_id, icon, extra_domains=None):
    dork_findings = _site_dork_search(domain, name_for_dork, subject_id, icon=icon)
    if extra_domains:
        for extra in extra_domains:
            dork_findings += _site_dork_search(
                extra, name_for_dork, subject_id, icon=icon
            )
        seen_urls = set()
        deduped = []
        for f in dork_findings:
            url = f.get("source_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped.append(f)
        return deduped, seen_urls
    else:
        seen_urls = set()
        for f in dork_findings:
            url = f.get("source_url")
            if url:
                seen_urls.add(url)
        return dork_findings, seen_urls


def _make_add_api_finding(
    findings,
    seen_urls,
    platform_name,
    source_type,
    icon,
    subject_id,
    credit_action_type=None,
):
    def add_api_finding(name, url, detail=""):
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        if credit_action_type:
            _use_credit(credit_action_type)
        findings.append(
            {
                "title": f"{platform_name}: {name}",
                "detail": detail or url or name,
                "source_url": url or "",
                "source_type": source_type,
                "icon": icon,
                "verified": False,
                "subject_id": subject_id,
                "screenshots": [{"url": None, "source_url": url}] if url else [],
            }
        )

    return add_api_finding


def _is_username(query):
    return "/" not in query and " " not in query and len(query) < 100


# ─── Platform handlers ──────────────────────────────────────────


def _facebook_check(action):
    result = _resolve_query(action)
    if result is None:
        return []
    query, subject_id, name_for_dork = result

    findings, seen_urls = _run_dork_search(
        "facebook.com", name_for_dork, subject_id, icon="📘"
    )
    add_api_finding = _make_add_api_finding(
        findings,
        seen_urls,
        "Facebook",
        "facebook",
        "📘",
        subject_id,
        credit_action_type="facebook",
    )

    api_key = _get_api_key("rapidapi_username_key")
    if not api_key or not _has_credits("facebook"):
        return findings

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
            r = jittered_get(
                PLATFORM_API_URLS["facebook_pullapi"],
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
                f"{PLATFORM_API_URLS['facebook_scraper3']}{path}",
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
            except json.JSONDecodeError:
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
    result = _resolve_query(action)
    if result is None:
        return []
    query, subject_id, name_for_dork = result

    findings, seen_urls = _run_dork_search(
        "tiktok.com", name_for_dork, subject_id, icon="🎵"
    )
    add_api_finding = _make_add_api_finding(
        findings, seen_urls, "TikTok", "tiktok", "🎵", subject_id
    )

    api_key = _get_api_key("rapidapi_username_key")
    if not api_key or not _has_credits("tiktok"):
        return findings

    is_username = _is_username(query)

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "scraptik.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = jittered_get(
                PLATFORM_API_URLS["tiktok_user"],
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
                logger.warning("TikTok API rate limit — fallback to name search")
            elif r.status_code in (401, 403):
                logger.warning("TikTok API auth error — fallback to name search")

        # Name search fallback
        r = jittered_get(
            PLATFORM_API_URLS["tiktok_search"],
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
    result = _resolve_query(action)
    if result is None:
        return []
    query, subject_id, name_for_dork = result

    findings, seen_urls = _run_dork_search(
        "instagram.com", name_for_dork, subject_id, icon="📸"
    )
    add_api_finding = _make_add_api_finding(
        findings, seen_urls, "Instagram", "instagram", "📸", subject_id
    )

    api_key = _get_api_key("rapidapi_username_key")
    if not api_key or not _has_credits("instagram"):
        return findings

    is_username = _is_username(query)

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "pro-social.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = jittered_get(
                PLATFORM_API_URLS["instagram_userinfo"],
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
                logger.warning("Instagram API rate limit — fallback to name search")
            elif r.status_code in (401, 403):
                logger.warning("Instagram API auth error — fallback to name search")

        # Name search fallback
        r = jittered_get(
            PLATFORM_API_URLS["instagram_search"],
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
    result = _resolve_query(action)
    if result is None:
        return []
    query, subject_id, name_for_dork = result

    findings, seen_urls = _run_dork_search(
        "linkedin.com", name_for_dork, subject_id, icon="💼"
    )
    add_api_finding = _make_add_api_finding(
        findings, seen_urls, "LinkedIn", "linkedin", "💼", subject_id
    )

    api_key = _get_api_key("rapidapi_username_key")
    if not api_key or not _has_credits("linkedin"):
        return findings

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
                f"{PLATFORM_API_URLS['linkedin_base']}{path}",
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
            except json.JSONDecodeError:
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
    result = _resolve_query(action)
    if result is None:
        return []
    query, subject_id, name_for_dork = result

    findings, seen_urls = _run_dork_search(
        "twitter.com", name_for_dork, subject_id, icon="🐦", extra_domains=["x.com"]
    )
    add_api_finding = _make_add_api_finding(
        findings, seen_urls, "Twitter", "twitter", "🐦", subject_id
    )

    api_key = _get_api_key("rapidapi_username_key")
    if not api_key or not _has_credits("twitter"):
        return findings

    is_username = _is_username(query)

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "twitter-api45.p.rapidapi.com",
            "Accept": "application/json",
        }

        if is_username:
            r = jittered_get(
                PLATFORM_API_URLS["twitter_screenname"],
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
                logger.warning("Twitter API rate limit — fallback to name search")
            elif r.status_code in (401, 403):
                logger.warning("Twitter API auth error — fallback to name search")

        # Search fallback
        r = jittered_get(
            PLATFORM_API_URLS["twitter_search"],
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
