"""Explainable quality signals for automatically created workflow findings.

This module scores evidence quality, not person identity. A high score means
that the source looks specific and technically credible; it never promotes a
finding to ``verified``.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source"}
_PLATFORM_HOSTS = {
    "facebook.com": "facebook", "instagram.com": "instagram",
    "linkedin.com": "linkedin", "reddit.com": "reddit", "tiktok.com": "tiktok",
    "twitch.tv": "twitch", "x.com": "x", "twitter.com": "x",
    "youtube.com": "youtube", "youtu.be": "youtube", "pinterest.com": "pinterest",
    "telegram.me": "telegram", "t.me": "telegram", "whatsapp.com": "whatsapp",
}
_GENERIC_PATHS = {"", "about", "explore", "home", "login", "live", "search", "signup"}


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
    score = max(0, min(100, score))
    confidence = "high" if score >= 60 else "medium" if score >= 45 else "low"
    return {
        "score": score, "confidence": confidence, "platform": platform,
        "canonical_url": canonical_url or None, "reasons": reasons,
        "warnings": warnings, "requires_human_validation": True,
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
