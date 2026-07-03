"""
Social media URL parsing using social-links library.

Wraps sociallinks (github.com/ysskrishna/social-links) to extract
usernames and detect platforms from social media URLs.
Falls back to naive extraction when social-links can't parse a URL.
"""

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from sociallinks import detect_platform as _sl_detect, extract_id as _sl_extract

    _HAS_SOCIALLINKS = True
except ImportError:
    _HAS_SOCIALLINKS = False
    logger.warning("social-links not installed — using fallback extraction")

_PLATFORM_ALIASES = {
    "x": "twitter",
}

MAJOR_SOCIAL_PLATFORMS = {
    "facebook",
    "instagram",
    "twitter",
    "x",
    "tiktok",
    "linkedin",
    "youtube",
    "snapchat",
    "reddit",
    "pinterest",
    "telegram",
    "whatsapp",
    "signal",
    "discord",
    "twitch",
    "tumblr",
    "vk",
    "wechat",
    "threads",
    "mastodon",
    "bluesky",
}


def detect_platform(url):
    """Detect social media platform from a URL. Returns normalized platform name."""
    if not url:
        return None
    if _HAS_SOCIALLINKS:
        try:
            platform = _sl_detect(url)
            if platform:
                return _PLATFORM_ALIASES.get(platform, platform)
        except Exception:
            logger.debug("Platform alias resolution failed for %s", url)
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "").split(".")[0]
    return domain if domain and domain != urlparse(url).netloc else None


def extract_username(url, platform=None):
    """Extract username/profile ID from a social media URL.

    Uses social-links first, falls back to naive path parsing.
    Returns the username string or None.
    """
    if not url:
        return None
    if _HAS_SOCIALLINKS:
        try:
            p = platform or _sl_detect(url)
            if p:
                uid = _sl_extract(p, url)
                if uid:
                    return uid.lstrip("@")
        except Exception:
            logger.debug("Social UID extraction failed for %s", url)
    path = urlparse(url).path.strip("/")
    if path:
        return path.split("/")[-1].split("?")[0]
    return None
