"""Server-side Cloudflare Turnstile verification for public forms."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import requests
from flask import current_app

logger = logging.getLogger(__name__)
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def is_required() -> bool:
    return _as_bool(current_app.config.get("TURNSTILE_REQUIRED", False))


def is_configured() -> bool:
    return bool(
        current_app.config.get("TURNSTILE_SITE_KEY")
        and current_app.config.get("TURNSTILE_SECRET_KEY")
    )


def is_enabled() -> bool:
    return is_configured() or is_required()


def verify(token: str | None, remote_ip: str | None = None) -> tuple[bool, str]:
    """Verify one Turnstile token; fail closed when protection is required."""
    if not is_enabled():
        return True, "disabled"
    if not is_configured():
        return False, "not_configured"
    if not token:
        logger.warning("Turnstile token missing from registration request")
        return False, "missing_token"

    payload = {
        "secret": current_app.config["TURNSTILE_SECRET_KEY"],
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        response = requests.post(VERIFY_URL, data=payload, timeout=5)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Turnstile verification failed: %s", exc)
        return False, "verification_unavailable"

    if not isinstance(result, Mapping):
        return False, "invalid_response"
    if result.get("success") is True:
        allowed_hosts = {
            host.strip().lower()
            for host in str(current_app.config.get("TURNSTILE_ALLOWED_HOSTNAMES", "")).split(",")
            if host.strip()
        }
        hostname = str(result.get("hostname") or "").lower()
        if allowed_hosts and hostname not in allowed_hosts:
            logger.warning("Turnstile returned an unexpected hostname: %s", hostname)
            return False, "hostname_mismatch"
        expected_action = str(current_app.config.get("TURNSTILE_ACTION", "")).strip()
        if expected_action and result.get("action") != expected_action:
            logger.warning(
                "Turnstile action mismatch: expected=%s returned=%s",
                expected_action,
                result.get("action"),
            )
            return False, "action_mismatch"
        return True, "verified"
    error_codes = ",".join(result.get("error-codes") or []) or "rejected"
    logger.warning("Turnstile rejected token: %s", error_codes)
    return False, error_codes
