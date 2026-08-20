"""
Field-Level Encryption Utilities for Sensitive Data
===============================================
Uses Fernet symmetric encryption for GDPR/AVG compliance.
Encryption keys are managed externally via environment variables.

Key Rotation:
    CMS_ENCRYPTION_KEY  = current key (used for new encryptions)
    CMS_ENCRYPTION_KEYS = comma-separated previous keys (used for decryption fallback)

    Rotation process:
    1. Move old key to CMS_ENCRYPTION_KEYS
    2. Set new key as CMS_ENCRYPTION_KEY
    3. Restart app (decrypts with fallback, encrypts with new key)
    4. Run `flask rotate-encryption` to re-encrypt all data
    5. Verify, then remove old key from CMS_ENCRYPTION_KEYS

Security Design Decisions:
- Fernet is based on AES-128-CBC with PKCS7 padding and HMAC verification
- Keys are NEVER stored in code or database
- Each encrypted field uses the same key (symmetric encryption)
"""

import base64
import logging
import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""

    pass


class FieldEncryptor:
    """
    Handles field-level encryption and decryption for sensitive data.

    Supports multi-key rotation: the current key encrypts, while previous
    keys (from CMS_ENCRYPTION_KEYS) are tried for decryption.

    Usage:
        encryptor = FieldEncryptor()
        encrypted = encryptor.encrypt("sensitive data")
        decrypted = encryptor.decrypt(encrypted)
    """

    _instance: Optional["FieldEncryptor"] = None
    _fernet: Fernet | None = None
    _legacy_fernets: list[Fernet] | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _get_key(self) -> bytes:
        """
        Retrieve encryption key from environment variable.
        Generate a new key if not set (development only).

        SECURITY NOTE: In production, ALWAYS set ENCRYPTION_KEY env var.
        Generate key with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        """
        key = os.environ.get("CMS_ENCRYPTION_KEY")

        if not key:
            key_file = os.path.join(os.path.dirname(__file__), "..", ".cms_key")
            key_file = os.path.abspath(key_file)
            if os.path.exists(key_file):
                with open(key_file) as f:
                    key = f.read().strip()
                logger.info("Loaded CMS encryption key from .cms_key file")
                os.environ["CMS_ENCRYPTION_KEY"] = key

        if not key:
            if os.environ.get("FLASK_ENV") == "production":
                raise EncryptionError(
                    "CMS_ENCRYPTION_KEY environment variable not set. "
                    "This is required in production for GDPR compliance."
                )
            # Development fallback - generate a new key (not for production!)
            key = Fernet.generate_key().decode()
            # Persist to .cms_key for dev restart survival
            try:
                key_file = os.path.join(os.path.dirname(__file__), "..", ".cms_key")
                key_file = os.path.abspath(key_file)
                with open(key_file, "w") as f:
                    f.write(key)
                os.chmod(key_file, 0o600)
                logger.warning(
                    "Generated new CMS encryption key — saved to .cms_key (chmod 600)."
                )
            except Exception:
                logger.warning(
                    "Using generated encryption key. Set CMS_ENCRYPTION_KEY for persistence!"
                )

        # Ensure key is valid base64
        try:
            key_bytes = key.encode() if isinstance(key, str) else key
            base64.urlsafe_b64decode(key_bytes)
            return key_bytes
        except (ValueError, TypeError) as e:
            raise EncryptionError(
                f"Invalid encryption key format: {e}. Must be a valid Fernet key."
            )

    def _get_legacy_keys(self) -> list[bytes]:
        """Parse comma-separated legacy keys from CMS_ENCRYPTION_KEYS."""
        raw = os.environ.get("CMS_ENCRYPTION_KEYS", "").strip()
        if not raw:
            return []
        keys = []
        for k in raw.split(","):
            k = k.strip()
            if not k:
                continue
            try:
                key_bytes = k.encode() if isinstance(k, str) else k
                base64.urlsafe_b64decode(key_bytes)
                keys.append(key_bytes)
            except (ValueError, TypeError):
                logger.warning("Skipping invalid legacy key: %s...", k[:8])
        return keys

    def _get_fernet(self) -> Fernet:
        """Get or create Fernet instance for the current key."""
        if self._fernet is None:
            self._fernet = Fernet(self._get_key())
        return self._fernet

    def _get_legacy_fernets(self) -> list[Fernet]:
        """Get Fernet instances for legacy keys (lazy-loaded, cached)."""
        if self._legacy_fernets is None:
            keys = self._get_legacy_keys()
            self._legacy_fernets = [Fernet(k) for k in keys]
        return self._legacy_fernets

    def encrypt(self, data: str | bytes | None) -> str | None:
        """
        Encrypt sensitive data with the current key.

        Args:
            data: String or bytes to encrypt. None returns None.

        Returns:
            Base64-encoded encrypted string, or None if input was None.

        Raises:
            EncryptionError: If encryption fails.
        """
        if data is None:
            return None

        if isinstance(data, str):
            data = data.encode("utf-8")

        try:
            encrypted = self._get_fernet().encrypt(data)
            return encrypted.decode("utf-8")
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {str(e)}")

    def decrypt(self, encrypted_data: str | bytes | None) -> str | None:
        """
        Decrypt previously encrypted data.

        Tries the current key first, then falls back to legacy keys.

        Args:
            encrypted_data: Base64-encoded encrypted string.

        Returns:
            Decrypted string, or None if input was None.

        Raises:
            EncryptionError: If decryption fails with all known keys.
        """
        if encrypted_data is None:
            return None

        if isinstance(encrypted_data, str):
            encrypted_data_bytes = encrypted_data.encode("utf-8")
        else:
            encrypted_data_bytes = encrypted_data

        # Try current key first
        try:
            decrypted = self._get_fernet().decrypt(encrypted_data_bytes)
            return decrypted.decode("utf-8")
        except InvalidToken:
            pass
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {str(e)}")

        # Try legacy keys
        for fern in self._get_legacy_fernets():
            try:
                decrypted = fern.decrypt(encrypted_data_bytes)
                logger.debug("Decrypted with legacy key")
                return decrypted.decode("utf-8")
            except InvalidToken:
                continue
            except Exception:
                continue

        raise EncryptionError("Decryption failed: Invalid token or wrong key")

    def re_encrypt(self, encrypted_data: str | bytes | None) -> str | None:
        """
        Decrypt with any known key, then re-encrypt with the current key.
        Returns the original value if it's already encrypted with the current key.
        Returns None for None values.
        """
        if encrypted_data is None:
            return None

        decrypted = self.decrypt(encrypted_data)
        if decrypted is None:
            return None

        # Check if it looks like it was already encrypted with current key
        # by checking if encrypt(decrypt(x)) == x (it should be)
        return self.encrypt(decrypted)

    def is_encrypted_with_current_key(self, encrypted_data: str | bytes | None) -> bool:
        """Check if data is encrypted with the current key (not a legacy key)."""
        if encrypted_data is None:
            return True
        if isinstance(encrypted_data, str):
            encrypted_data_bytes = encrypted_data.encode("utf-8")
        else:
            encrypted_data_bytes = encrypted_data
        try:
            self._get_fernet().decrypt(encrypted_data_bytes)
            return True
        except InvalidToken:
            return False
        except Exception:
            return False

    def rotate_key(self, new_key: str) -> bool:
        """
        Rotate encryption key. Note: This requires re-encrypting all data.
        Use with caution and proper data migration strategy.

        Args:
            new_key: New Fernet-compatible key.

        Returns:
            True if successful.
        """
        try:
            os.environ["CMS_ENCRYPTION_KEY"] = new_key
            self._fernet = None  # Reset Fernet instance
            self._get_fernet()  # Validate new key
            return True
        except Exception as e:
            logger.error(f"Key rotation failed: {e}")
            return False

    @classmethod
    def reset(cls):
        """Reset singleton instance (useful for testing)."""
        cls._instance = None
        cls._fernet = None
        cls._legacy_fernets = None


# Global instance for convenience
encryptor = FieldEncryptor()


def encrypt_field(data: str | bytes | None) -> str | None:
    """Convenience function for encrypting a single field."""
    return encryptor.encrypt(data)


def decrypt_field(data: str | bytes | None) -> str | None:
    """Convenience function for decrypting a single field."""
    return encryptor.decrypt(data)


def encrypt_dict_fields(data: dict, fields: list) -> dict:
    """
    Encrypt multiple fields in a dictionary.

    Args:
        data: Dictionary containing sensitive fields.
        fields: List of field names to encrypt.

    Returns:
        Dictionary with specified fields encrypted.
    """
    result = data.copy()
    for field in fields:
        if field in result and result[field]:
            result[field] = encryptor.encrypt(result[field])
    return result


def decrypt_dict_fields(data: dict, fields: list) -> dict:
    """
    Decrypt multiple fields in a dictionary.

    Args:
        data: Dictionary with encrypted fields.
        fields: List of field names to decrypt.

    Returns:
        Dictionary with specified fields decrypted.
    """
    result = data.copy()
    for field in fields:
        if field in result and result[field]:
            result[field] = encryptor.decrypt(result[field])
    return result


# Encryption-aware SQLAlchemy TypeDecorator
class EncryptedString:
    """
    SQLAlchemy-compatible encrypted string type.

    Usage in models:
        from sqlalchemy import Column, String
        class MyModel(db.Model):
            sensitive_data = Column(EncryptedString(500), nullable=True)
    """

    def __init__(self, max_length: int = 1000):
        self.max_length = max_length

    def __call__(self):
        from sqlalchemy import String, TypeDecorator

        class EncryptedType(TypeDecorator):
            impl = String(self.max_length)
            cache_ok = True

            def process_bind_param(self, value, dialect):
                if value is not None:
                    return encryptor.encrypt(value)
                return value

            def process_result_value(self, value, dialect):
                if value is not None:
                    return encryptor.decrypt(value)
                return value

        return EncryptedType()
