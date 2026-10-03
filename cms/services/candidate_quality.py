"""Explainable quality signals for automatically created workflow findings.

This module scores evidence quality, not person identity. A high score means
that the source looks specific and technically credible; it never promotes a
finding to ``verified``.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cms.encryption_utils import encryptor

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source"}
_PLATFORM_HOSTS = {
    "facebook.com": "facebook", "instagram.com": "instagram",
    "linkedin.com": "linkedin", "reddit.com": "reddit", "tiktok.com": "tiktok",
    "twitch.tv": "twitch", "x.com": "x", "twitter.com": "x",
    "youtube.com": "youtube", "youtu.be": "youtube", "pinterest.com": "pinterest",
    "telegram.me": "telegram", "t.me": "telegram", "whatsapp.com": "whatsapp",
}
_GENERIC_PATHS = {"", "about", "explore", "home", "login", "live", "search", "signup"}


def _plain(value) -> str:
    """Read a possibly encrypted value without mutating the model."""
    if value in (None, ""):
        return ""
    value = str(value).strip()
    try:
        return str(encryptor.decrypt(value) or "").strip()
    except Exception:
        return value


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _subject_signals(subject) -> dict[str, set[str]]:
    """Collect comparison signals while excluding initials and email domains."""
    empty = {"names": set(), "usernames": set(), "emails": set(), "phones": set(), "platforms": set()}
    if subject is None:
        return empty
    names = set()
    for field in ("name", "voornamen", "achternaam", "tussenvoegsels"):
        value = _plain(getattr(subject, field, None))
        if value and len(value) >= 3 and not re.fullmatch(r"[A-Za-z]", value):
            names.add(value.casefold())
    usernames, platforms = set(), set()
    for raw in (getattr(subject, "social_media_ids", None), getattr(subject, "workflow_social_accounts", None)):
        if isinstance(raw, dict):
            for platform, item in raw.items():
                platforms.add(str(platform).casefold())
                if isinstance(item, dict):
                    item = item.get("username") or item.get("handle") or item.get("id")
                if item:
                    usernames.add(str(item).strip().lstrip("@").casefold())
        elif isinstance(raw, (list, tuple, set)):
            usernames.update(str(item).strip().lstrip("@").casefold() for item in raw if item)
    emails, phones = set(), set()
    for value in (_plain(getattr(subject, "email", None)), _plain(getattr(subject, "phone", None))):
        if "@" in value:
            emails.add(value.casefold())
        elif value:
            phones.add(re.sub(r"\D", "", value))
    return {"names": names, "usernames": usernames, "emails": emails, "phones": phones, "platforms": platforms}


def _identity_signals(finding, platform: str | None) -> tuple[int, list[str], list[str]]:
    """Score exact subject evidence separately from source quality."""
    signals = _subject_signals(getattr(finding, "subject", None))
    if not any(signals.values()):
        return 0, [], []
    haystack = " ".join(
        str(getattr(finding, field, "") or "")
        for field in ("title", "content", "detail", "source_url")
    ).casefold()
    compact_haystack = _compact(haystack)
    matches, warnings = [], []
    score = 0
    for email in sorted(signals["emails"]):
        local = email.split("@", 1)[0]
        if email in haystack:
            score += 18
            matches.append("exact e-mailadres in de bron")
        elif local and len(local) >= 4 and _compact(local) in compact_haystack:
            score += 10
            matches.append("lokale deel van e-mailadres in de bron")
    digits_haystack = re.sub(r"\D", "", haystack)
    for phone in sorted(signals["phones"]):
        if len(phone) >= 7 and phone in digits_haystack:
            score += 18
            matches.append("telefoonnummer in de bron")
    for username in sorted(signals["usernames"]):
        compact_username = _compact(username)
        if len(compact_username) >= 4 and compact_username in compact_haystack:
            score += 16
            matches.append("bekende gebruikersnaam in de bron")
    for name in sorted(signals["names"], key=len, reverse=True):
        tokens = [token for token in re.findall(r"[a-z0-9]+", name.casefold()) if len(token) >= 3]
        compact_name = _compact(name)
        if len(tokens) >= 2 and compact_name in compact_haystack:
            score += 14
            matches.append("volledige naam in de bron")
            break
        if len(tokens) >= 2 and sum(token in compact_haystack for token in tokens) >= 2:
            score += 8
            matches.append("meerdere naamdelen in de bron")
            break
    if platform and signals["platforms"] and any(
        platform in item or item in platform for item in signals["platforms"]
    ):
        score += 6
        matches.append("platform komt overeen met bekende subjectgegevens")
    if not matches:
        warnings.append("geen exacte overeenkomst met bekende subjectgegevens")
    return min(score, 40), matches, warnings


def canonicalize_url(value: str | None) -> str:
    """Return a stable URL for comparison and duplicate detection."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return raw
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = host
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    query = urlencode(sorted(
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
    ))
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def platform_for_url(value: str | None) -> str | None:
    """Return a platform only for an exact host or subdomain match."""
    host = (urlsplit(canonicalize_url(value)).hostname or "").lower()
    for domain, platform in _PLATFORM_HOSTS.items():
        if host == domain or host.endswith(f".{domain}"):
            return platform
    return None


def assess_finding(finding) -> dict:
    """Calculate conservative, explainable source-quality signals."""
    title = str(getattr(finding, "title", "") or "")
    content = str(getattr(finding, "content", "") or "")
    detail = str(getattr(finding, "detail", "") or "")
    source_url = str(getattr(finding, "source_url", "") or "").strip()
    haystack = f"{title}\n{content}\n{detail}".casefold()
    canonical_url = canonicalize_url(source_url)
    platform = platform_for_url(source_url)
    score = 0
    reasons = []
    warnings = []
    if canonical_url:
        score += 10
        reasons.append("bron bevat een concrete URL")
    else:
        warnings.append("geen concrete bron-URL")
    if platform:
        score += 15
        reasons.append(f"URL hoort bij platform {platform}")
        path_parts = [part for part in urlsplit(canonical_url).path.split("/") if part]
        last_part = path_parts[-1].casefold() if path_parts else ""
        if last_part and last_part not in _GENERIC_PATHS:
            score += 15
            reasons.append("URL bevat een specifieke profielreferentie")
        else:
            score -= 20
            warnings.append("URL lijkt generiek en niet profielspecifiek")
    if re.search(r"\b(status\s*:\s*confirmed|account found|profile active)\b", haystack):
        score += 25
        reasons.append("bron meldt expliciet een bevestigd of actief account")
    if re.search(r"\b(responded with 200|no confirmation|likely false positive|generic page)\b", haystack):
        score -= 35
        warnings.append("bron bevat een bekend false-positive signaal")
    if re.search(r"\b(search result|google search|browser search|dork)\b", haystack):
        score -= 10
        warnings.append("resultaat komt uit een zoeklaag en niet rechtstreeks uit een profiel")
    identity_score, identity_matches, identity_warnings = _identity_signals(finding, platform)
    if identity_score:
        score += identity_score
        reasons.extend(identity_matches)
    warnings.extend(identity_warnings)
    score = max(0, min(100, score))
    confidence = "high" if score >= 60 else "medium" if score >= 45 else "low"
    return {
        "score": score, "confidence": confidence, "platform": platform,
        "canonical_url": canonical_url or None, "reasons": reasons,
        "warnings": warnings, "requires_human_validation": True,
        "identity_score": identity_score,
        "identity_matches": identity_matches,
    }


def enrich_finding_quality(finding) -> dict:
    """Persist the latest quality assessment in the finding's raw metadata."""
    raw = getattr(finding, "raw_data", None)
    if not isinstance(raw, dict):
        raw = {}
    quality = assess_finding(finding)
    raw["candidate_quality"] = quality
    finding.raw_data = raw
    if not getattr(finding, "confidence_level", None):
        finding.confidence_level = quality["confidence"]
    return quality


def finding_sort_key(finding, sort: str = "newest"):
    """Return a stable UI sort key for a finding or finding DTO."""
    quality = getattr(finding, "candidate_quality", None) or assess_finding(finding)
    created = getattr(finding, "created_at", None)
    created_key = created.isoformat() if created else ""
    if sort == "quality":
        return (-int(quality.get("score", 0)), -int(quality.get("identity_score", 0)), created_key)
    if sort == "relevance":
        return (-int(quality.get("identity_score", 0)), -int(quality.get("score", 0)), created_key)
    if sort == "source":
        return (
            str(quality.get("platform") or getattr(finding, "source_type", "") or "").casefold(),
            str(getattr(finding, "source_url", "") or "").casefold(),
            created_key,
        )
    return ("", "", created_key)
