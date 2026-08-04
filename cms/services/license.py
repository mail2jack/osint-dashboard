"""Offline-verifiable Ed25519 license client.

The app ships a public key (default committed below; override via the
`license_public_key` Setting or `LICENSE_PUBLIC_KEY` env). During each
telemetry check-in the server's signed license (claims + signature) is fetched
and cached in the Setting table. Validity is verified locally with the public
key, so a down license server never breaks the app; only revocation needs a
successful online check-in and is applied on the next one.

Enforcement is "soft trial" by default: the app keeps running, but feature
gates (tenants, AI, external integrations) stay limited until a valid full
license is seen. `LICENSE_ENFORCEMENT=off` disables the gates entirely.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_KEY = "4xvSvYw1F9tjTfss0e_6XpdUnPxiOaFdK0shP3cxz-U"


def _b64dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _get_setting(key, default=None):
    try:
        from cms.models import Setting

        value = Setting.get(key)
        return value if value not in (None, "") else default
    except Exception:
        return os.environ.get(key.upper(), default)


def _set_setting(key, value, description=""):
    try:
        from cms.models import Setting

        Setting.set(key, value, category="system", description=description)
        return True
    except Exception:
        logger.debug("Could not store setting %s", key, exc_info=True)
        return False


def get_public_key() -> str:
    value = str(_get_setting("license_public_key", "") or "").strip()
    if value:
        return value
    value = os.environ.get("LICENSE_PUBLIC_KEY", "").strip()
    return value or DEFAULT_PUBLIC_KEY


def canonical_payload(claims: dict) -> str:
    return json.dumps(claims, separators=(",", ":"), sort_keys=True)


def verify_signature(claims: dict, signature: str) -> bool:
    """Return True when `signature` (base64url) signs `claims` with our key."""
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            _b64dec(get_public_key())
        )
        public_key.verify(_b64dec(signature), canonical_payload(claims).encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError, Exception):
        return False


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def cache_license(license_obj) -> bool:
    """Verify and cache the signed license returned by the server."""
    if not isinstance(license_obj, dict):
        return False
    payload = license_obj.get("payload")
    signature = license_obj.get("signature")
    status = str(license_obj.get("status", "active") or "active")
    if not payload or not signature:
        _clear_license()
        return False
    try:
        claims = json.loads(payload)
    except (ValueError, TypeError):
        return False
    try:
        from cms.services.telemetry import get_install_id
    except Exception:
        get_install_id = None
    if get_install_id:
        claims_id = claims.get("install_id")
        if claims_id and claims_id != get_install_id():
            return False
    if not verify_signature(claims, signature):
        logger.warning("License signature verification failed — ignoring")
        return False
    _set_setting("license_payload", payload, "Signed license payload (verified)")
    _set_setting("license_signature", signature, "Ed25519 license signature")
    _set_setting("license_status", status, "License status from the license server")
    return True


def _clear_license() -> None:
    _set_setting("license_payload", "")
    _set_setting("license_signature", "")
    _set_setting("license_status", "")


def get_license_state() -> dict:
    """Current license state, verified offline against the public key.

    Keys: present, valid, plan, expires_at, days_left, status, revoked, message.
    """
    state = {
        "present": False,
        "valid": False,
        "plan": None,
        "expires_at": None,
        "days_left": None,
        "status": None,
        "revoked": False,
        "message": None,
    }
    payload = _get_setting("license_payload", None)
    signature = _get_setting("license_signature", None)
    if not payload or not signature:
        state["message"] = "Trial mode (no license installed)"
        return state
    try:
        claims = json.loads(payload)
    except (ValueError, TypeError):
        state["message"] = "License data is corrupt"
        return state
    state["present"] = True
    state["plan"] = claims.get("plan")
    state["expires_at"] = claims.get("expires_at")
    try:
        from cms.services.telemetry import get_install_id

        claims_id = claims.get("install_id")
        if claims_id and get_install_id() and claims_id != get_install_id():
            state["message"] = "License belongs to a different install"
            return state
    except Exception:
        pass
    if not verify_signature(claims, signature):
        state["message"] = "License signature invalid"
        return state
    state["valid"] = True
    expires = _parse_ts(state["expires_at"])
    days_left = None
    if expires:
        days_left = (expires - datetime.now(timezone.utc)).days
        state["days_left"] = days_left
    status = str(_get_setting("license_status", "active") or "active")
    state["status"] = status
    if status == "revoked":
        state["revoked"] = True
        state["valid"] = False
        state["message"] = "License revoked by the license server"
    elif expires and days_left is not None and days_left < 0:
        state["valid"] = False
        state["message"] = "License expired"
    elif days_left is not None and days_left <= 14:
        state["message"] = f"License expires in {days_left} days"
    else:
        state["message"] = "License valid"
    return state


def is_licensed() -> bool:
    return bool(get_license_state()["valid"])


def enforcement_off() -> bool:
    return os.environ.get("LICENSE_ENFORCEMENT", "").lower() in ("off", "0", "false")


def trial_mode() -> bool:
    """True when features should be limited (no valid license, enforcement on)."""
    if enforcement_off():
        return False
    return not is_licensed()


TRIAL_GATED_FEATURES = ("ai", "spiderfoot", "vessel", "phone")


def trial_blocked(feature: str) -> bool:
    """True when the feature is gated because the install is on trial."""
    return trial_mode() and feature in TRIAL_GATED_FEATURES


def trial_tenant_limit() -> int:
    try:
        return int(_get_setting("trial_tenant_limit", "1") or "1")
    except (TypeError, ValueError):
        return 1
