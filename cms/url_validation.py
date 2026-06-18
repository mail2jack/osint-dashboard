"""
URL validation utilities to prevent SSRF attacks.
"""

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PRIVATE_PREFIXES = (
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
)


def validate_url(url: str, allow_private: bool = False) -> str:
    """Validate a URL against SSRF.

    - Only http/https schemes allowed
    - Private IPs blocked by default
    Returns the validated URL string or raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed")

    host = parsed.hostname
    if not host:
        raise ValueError("URL has no hostname")

    if not allow_private:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ValueError(f"Private IP address not allowed: {host}")
        except ValueError:
            host_lower = host.lower()
            if host_lower == "localhost" or host_lower.startswith(PRIVATE_PREFIXES):
                raise ValueError(f"Private host not allowed: {host}")

    if ":" in host and not host.endswith("]"):
        raise ValueError("IPv6 address must be enclosed in square brackets")

    return url
