"""SSRF protection helpers shared across fetch layers.

Every hop of a fetch is revalidated (curl redirects and Playwright
requests), so a DNS-rebinding attack or an HTTP redirect cannot smuggle a
request to a private/reserved network after the initial URL check.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def is_unsafe_address(addr_str: str) -> bool:
    """Return True if an IP literal is private/reserved and must never be reached."""
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(url: str) -> tuple[bool, str]:
    """Check scheme and resolved addresses of a URL.

    Returns (ok, reason). A host that cannot be resolved is allowed (the
    actual request will fail anyway); any resolved private/reserved address
    is rejected.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "unsupported scheme"
    host = parsed.hostname
    if not host:
        return False, "missing host"
    if is_unsafe_address(host):
        return False, "blocked address"
    try:
        addrs = socket.getaddrinfo(host, None)
        for _family, _type, _proto, _canon, sockaddr in addrs:
            if is_unsafe_address(sockaddr[0]):
                return False, "resolves to blocked address"
    except (socket.gaierror, OSError):
        pass
    return True, ""


def validate_capture_url(url: str) -> tuple[bool, str]:
    """Strict target validation for browser capture requests.

    Unlike legacy fetch helpers, a capture request must resolve now.  This is
    fail-closed for DNS failures; the future worker will revalidate every
    browser request/redirect hop before connecting as a second boundary.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "unsupported scheme"
    if not parsed.hostname or parsed.username or parsed.password:
        return False, "invalid host"
    if is_unsafe_address(parsed.hostname):
        return False, "blocked address"
    try:
        addrs = socket.getaddrinfo(parsed.hostname, None)
    except (socket.gaierror, OSError):
        return False, "host could not be resolved"
    if not addrs:
        return False, "host could not be resolved"
    if any(is_unsafe_address(sockaddr[0]) for *_prefix, sockaddr in addrs):
        return False, "resolves to blocked address"
    return True, ""


def _guard_handler(route, request) -> None:
    ok, reason = validate_url(request.url)
    if not ok:
        logger.info("SSRF guard blocked %s (%s)", request.url, reason)
        try:
            route.fulfill(
                status=403,
                content_type="text/plain",
                body="Blocked by SSRF guard",
            )
            return
        except Exception:
            try:
                route.abort()
                return
            except Exception:
                logger.debug(
                    "SSRF guard could not block %s", request.url, exc_info=True
                )
                return
    route.continue_()


def install_request_guard(page) -> None:
    """Block any Playwright request that resolves to a private/reserved address."""
    page.route("**/*", _guard_handler)
