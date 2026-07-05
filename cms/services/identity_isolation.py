"""Identity isolation — per-onderzoek apart Tor circuit.

Gebruikt Tor's ``IsolateSOCKSAuth``-mechanisme: elke identity krijgt een
unieke SOCKS5-username, waardoor Tor voor elke identity een apart circuit
gebruikt. Vereist ``IsolateSOCKSAuth 1`` in ``torrc``.

Voorbeeld torrc:
    SOCKSPort 9050 IsolateSOCKSAuth
"""

import hashlib
import logging
import os
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_current_identity: ContextVar[str | None] = ContextVar(
    "_current_identity", default=None
)

_IDENTITY_ENABLED: bool = False
_IDENTITY_LAST_CHECK: float = 0


def _refresh_config() -> None:
    global _IDENTITY_ENABLED, _IDENTITY_LAST_CHECK
    now = time.time()
    if now - _IDENTITY_LAST_CHECK < 60:
        return
    _IDENTITY_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("identity_isolation_enabled", "false")
            _IDENTITY_ENABLED = val.lower() in ("true", "1", "yes")
    except Exception:
        _IDENTITY_ENABLED = os.environ.get(
            "IDENTITY_ISOLATION_ENABLED", "false"
        ).lower() in ("true", "1", "yes")


def set_current_identity(identity: str | None) -> None:
    """Set the current identity (thread/request-scoped)."""
    _current_identity.set(identity)


def get_current_identity() -> str | None:
    """Get the current identity, or None if not set."""
    return _current_identity.get()


def set_identity_for_case(case_id: str) -> None:
    """Derive a Tor auth identity from a case_id."""
    hashed = hashlib.sha256(case_id.encode()).hexdigest()[:16]
    set_current_identity(f"case_{hashed}")


def reset_identity() -> None:
    """Clear the current identity."""
    set_current_identity(None)


def is_identity_isolation_enabled() -> bool:
    _refresh_config()
    return _IDENTITY_ENABLED


def identity_for_proxy(proxy_url: str, identity: str) -> str:
    """Insert identity as SOCKS5 username for Tor auth isolation.

    ``socks5h://127.0.0.1:9050`` → ``socks5h://<identity>:@127.0.0.1:9050``
    """
    if "@" in proxy_url:
        return proxy_url
    scheme, rest = proxy_url.split("://", 1)
    return f"{scheme}://{identity}:@{rest}"
