"""OSINT audit hash chain — cryptografische chain of custody.

Elke OSINT HTTP call wordt geregistreerd met een SHA256-hash die
 crypto-grafisch gelinkt is aan de vorige call. Dit creëert een
 tamper-evident log van alle OSINT-activiteit binnen een proces-lifecycle.

Bij een proces-herstart begint de chain opnieuw (genesis hash). De records
worden ook persistent opgeslagen in de AuditLog-tabel voor forensische analyse.
"""

import hashlib
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_CHAIN_LAST_HASH: str | None = None
_CHAIN_LENGTH: int = 0
_CHAIN_LOCK = threading.Lock()
_CHAIN_ENABLED: bool = True
_CHAIN_LAST_CHECK: float = 0
_GENESIS_HASH = "0" * 64


def _refresh_config() -> None:
    global _CHAIN_ENABLED, _CHAIN_LAST_CHECK
    now = time.time()
    if now - _CHAIN_LAST_CHECK < 60:
        return
    _CHAIN_LAST_CHECK = now
    try:
        from flask import current_app

        with current_app.app_context():
            from cms.models import Setting

            val = Setting.get("audit_chain_enabled", "true")
            _CHAIN_ENABLED = val.lower() in ("true", "1", "yes")
    except Exception:
        _CHAIN_ENABLED = os.environ.get("AUDIT_CHAIN_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )


def record_osint_call(
    url: str,
    method: str,
    status_code: int | str,
    domain: str | None = None,
    profile: str | None = None,
    error: str | None = None,
) -> dict | None:
    """Record an OSINT HTTP call in the audit hash chain.

    Returns the chain metadata dict (entry_hash, chain_hash, prev_hash, length)
    or None if chain is disabled.
    """
    global _CHAIN_LAST_HASH, _CHAIN_LENGTH
    _refresh_config()
    if not _CHAIN_ENABLED:
        return None

    ts = time.time()
    data = f"{url}|{method}|{status_code}|{domain or ''}|{profile or ''}|{error or ''}|{ts}"
    entry_hash = hashlib.sha256(data.encode()).hexdigest()

    with _CHAIN_LOCK:
        prev = _CHAIN_LAST_HASH or _GENESIS_HASH
        chain_hash = hashlib.sha256((prev + entry_hash).encode()).hexdigest()
        _CHAIN_LAST_HASH = chain_hash
        _CHAIN_LENGTH += 1
        length = _CHAIN_LENGTH

    metadata: dict[str, Any] = {
        "url": url,
        "method": method,
        "status_code": status_code,
        "domain": domain,
        "profile": profile,
        "error": error,
        "entry_hash": entry_hash,
        "chain_hash": chain_hash,
        "prev_hash": prev,
        "length": length,
        "timestamp": ts,
    }

    # Persist to AuditLog (requires app context, graceful degradation)
    try:
        from flask import has_app_context, current_app
        from flask_login import current_user

        if has_app_context():
            with current_app.app_context():
                from cms.models import AuditLog

                user_id = getattr(current_user, "id", None) or "system"
                err = f" ERROR: {error}" if error else ""
                desc = f"OSINT #{length}: {method} {url} -> {status_code}{err}"

                AuditLog.log(
                    user_id=user_id,
                    action="osint_call",
                    entity_type="osint_chain",
                    entity_id=chain_hash[:16],
                    ip_address=None,
                    description=desc[:500],
                    changes={"osint_chain": metadata},
                )
    except Exception:
        pass  # Never break OSINT calls if audit logging fails

    return metadata


def get_chain_status() -> dict[str, Any]:
    """Return the current chain state (enabled, length, last_hash)."""
    _refresh_config()
    with _CHAIN_LOCK:
        return {
            "enabled": _CHAIN_ENABLED,
            "length": _CHAIN_LENGTH,
            "last_hash": _CHAIN_LAST_HASH,
        }


def reset_chain() -> None:
    """Reset the in-memory chain (for testing)."""
    global _CHAIN_LAST_HASH, _CHAIN_LENGTH, _CHAIN_LAST_CHECK
    with _CHAIN_LOCK:
        _CHAIN_LAST_HASH = None
        _CHAIN_LENGTH = 0
    _CHAIN_LAST_CHECK = 0


def verify_chain() -> dict[str, Any]:
    """Verify the integrity of the audit chain by replaying from AuditLog.

    Reads all ``entity_type="osint_chain"`` entries in chronological order,
    recomputes the hashes, and compares against stored metadata.

    Returns:
        {"valid": bool, "entries": int, "errors": [str]}
    """
    result: dict[str, Any] = {"valid": True, "entries": 0, "errors": []}

    try:
        from flask import has_app_context, current_app

        if not has_app_context():
            result["valid"] = False
            result["errors"].append("No Flask app context")
            return result

        with current_app.app_context():
            from cms.models import AuditLog

            entries = (
                AuditLog.query.filter_by(entity_type="osint_chain")
                .order_by(AuditLog.timestamp.asc())
                .all()
            )
    except Exception as e:
        result["valid"] = False
        result["errors"].append(f"DB read error: {e}")
        return result

    if not entries:
        return result

    prev_hash = _GENESIS_HASH
    for entry in entries:
        result["entries"] += 1
        meta = entry.changes_made or {}
        chain_data = meta.get("osint_chain", meta)

        stored_entry_hash = chain_data.get("entry_hash")
        stored_chain_hash = chain_data.get("chain_hash")
        stored_prev_hash = chain_data.get("prev_hash")
        stored_url = chain_data.get("url")
        stored_method = chain_data.get("method")
        stored_status = chain_data.get("status_code")
        stored_domain = chain_data.get("domain", "")
        stored_profile = chain_data.get("profile", "")
        stored_error = chain_data.get("error", "")

        if not stored_entry_hash or not stored_chain_hash:
            result["errors"].append(f"Entry {entry.id}: missing hash fields")
            continue

        # Verify prev_hash links to previous entry
        if stored_prev_hash != prev_hash:
            result["errors"].append(
                f"Entry {entry.id}: prev_hash mismatch "
                f"(expected {prev_hash[:16]}..., got {stored_prev_hash[:16]}...)"
            )
            result["valid"] = False

        # Recompute entry_hash from stored data
        recomputed_entry = hashlib.sha256(
            f"{stored_url}|{stored_method}|{stored_status}|"
            f"{stored_domain or ''}|{stored_profile or ''}|"
            f"{stored_error or ''}|{chain_data.get('timestamp', '')}".encode()
        ).hexdigest()

        if recomputed_entry != stored_entry_hash:
            result["errors"].append(f"Entry {entry.id}: entry_hash mismatch")
            result["valid"] = False

        # Recompute chain_hash = SHA256(prev_hash + entry_hash)
        recomputed_chain = hashlib.sha256(
            (prev_hash + stored_entry_hash).encode()
        ).hexdigest()

        if recomputed_chain != stored_chain_hash:
            result["errors"].append(f"Entry {entry.id}: chain_hash mismatch")
            result["valid"] = False

        prev_hash = stored_chain_hash

    return result
