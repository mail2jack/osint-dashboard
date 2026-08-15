"""
Keyed HMAC Fingerprint Utilities (ADR-0001 D3)
=============================================
Stores a deterministic, keyed, HMAC-SHA256 fingerprint of a normalized
identifier in a companion column so encrypted data can be searched and
deduplicated without plaintext indexes or ILIKE on ciphertext.

Security design decisions:
- The fingerprint is keyed with a dedicated fingerprint key (CMS_FINGERPRINT_KEY),
  never the Fernet encryption key and never the plaintext itself.
- Fingerprints are not reversible to the plaintext without brute force over
  the (normalized) identifier space.
- The key is never stored in code or the database.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
from typing import Optional

logger = logging.getLogger(__name__)

FINGERPRINT_KEY_ENV = "CMS_FINGERPRINT_KEY"
_FINGERPRINT_KEY_FILE = ".cms_fp_key"
_WS_OR_SEPARATOR = re.compile(r"[\s\-_.]+")


class FingerprintError(Exception):
    """Raised when the fingerprint key is unavailable."""


class Fingerprinter:
    """Singleton that produces keyed HMAC-SHA256 fingerprints."""

    _instance: Optional["Fingerprinter"] = None
    _key: bytes | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _key_file_path(self) -> str:
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", _FINGERPRINT_KEY_FILE)
        )

    def _get_key(self) -> bytes:
        if self._key is not None:
            return self._key

        key = os.environ.get(FINGERPRINT_KEY_ENV)
        if not key:
            key_file = self._key_file_path()
            if os.path.exists(key_file):
                with open(key_file) as f:
                    key = f.read().strip()
                logger.info("Loaded fingerprint key from .cms_fp_key file")
                os.environ[FINGERPRINT_KEY_ENV] = key

        if not key:
            if os.environ.get("FLASK_ENV") == "production":
                raise FingerprintError(
                    f"{FINGERPRINT_KEY_ENV} environment variable not set. "
                    "Required in production for encrypted search (ADR-0001 D3)."
                )
            key = secrets.token_hex(32)
            try:
                with open(self._key_file_path(), "w") as f:
                    f.write(key)
                os.chmod(self._key_file_path(), 0o600)
                logger.warning(
                    "Generated new fingerprint key — saved to .cms_fp_key (chmod 600)."
                )
            except Exception:
                logger.warning(
                    "Using generated fingerprint key. Set %s for persistence!",
                    FINGERPRINT_KEY_ENV,
                )

        self._key = key.encode() if isinstance(key, str) else key
        return self._key

    def fingerprint(self, value: str) -> str:
        """Return the keyed HMAC-SHA256 hex digest of a normalized value."""
        return hmac.new(
            self._get_key(), str(value).encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def fingerprint_identifier(self, identifier_type: str, value: str) -> str:
        """Normalize the identifier, then fingerprint it (D3)."""
        return self.fingerprint(normalize_identifier(identifier_type, value))

    @classmethod
    def reset(cls):
        """Reset singleton state (useful for testing)."""
        cls._instance = None
        cls._key = None


# Global instance for convenience
fingerprinter = Fingerprinter()


def _get_fingerprinter() -> Fingerprinter:
    # Call the constructor so reset()/key rotation from tests or config
    # changes are picked up on the next use.
    return Fingerprinter()


def normalize_identifier(identifier_type: str, value: str | None) -> str:
    """Normalize an identifier so equal logical values fingerprint identically.

    Applies per-type normalization: lowercasing for email/handles, separator
    stripping for phone/IBAN/license plates/documents, and @-stripping for
    platform handles.
    """
    if value is None:
        return ""
    v = str(value).strip()
    t = (identifier_type or "").lower()

    if t in ("phone", "iban", "license_plate", "document", "bsn"):
        return _WS_OR_SEPARATOR.sub("", v).lower()
    if t in ("email", "platform_handle"):
        return v.lstrip("@").lower()
    return v.lower()


def fingerprint(value: str) -> str:
    """Convenience function for fingerprinting an already-normalized value."""
    return _get_fingerprinter().fingerprint(value)


def fingerprint_identifier(identifier_type: str, value: str) -> str:
    """Convenience function: normalize then fingerprint an identifier."""
    return _get_fingerprinter().fingerprint_identifier(identifier_type, value)
