"""
Field-Level Encryption Utilities for Sensitive Data
================================================
Uses Fernet symmetric encryption for GDPR/AVG compliance.
Encryption keys are managed externally via environment variables.

Security Design Decisions:
- Fernet is based on AES-128-CBC with PKCS7 padding and HMAC verification
- Keys are NEVER stored in code or database
- Each encrypted field uses the same key (symmetric encryption)
- For high-security scenarios, consider key rotation and key derivation functions (KDF)
"""

import base64
import os
from typing import Any, Optional, Union
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""
    pass


class FieldEncryptor:
    """
    Handles field-level encryption and decryption for sensitive data.
    
    Usage:
        encryptor = FieldEncryptor()
        encrypted = encryptor.encrypt("sensitive data")
        decrypted = encryptor.decrypt(encrypted)
    """
    
    _instance: Optional['FieldEncryptor'] = None
    _fernet: Optional[Fernet] = None
    
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
        key = os.environ.get('CMS_ENCRYPTION_KEY')
        
        if not key:
            if os.environ.get('FLASK_ENV') == 'production':
                raise EncryptionError(
                    "CMS_ENCRYPTION_KEY environment variable not set. "
                    "This is required in production for GDPR compliance."
                )
            # Development fallback - generate a new key (not for production!)
            key = Fernet.generate_key().decode()
            print("WARNING: Using generated encryption key. Set CMS_ENCRYPTION_KEY for production!")
        
        # Ensure key is valid base64
        try:
            key_bytes = key.encode() if isinstance(key, str) else key
            base64.urlsafe_b64decode(key_bytes)
            return key_bytes
        except Exception:
            raise EncryptionError("Invalid encryption key format. Must be a valid Fernet key.")
    
    def _get_fernet(self) -> Fernet:
        """Get or create Fernet instance."""
        if self._fernet is None:
            self._fernet = Fernet(self._get_key())
        return self._fernet
    
    def encrypt(self, data: Union[str, bytes, None]) -> Optional[str]:
        """
        Encrypt sensitive data.
        
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
            data = data.encode('utf-8')
        
        try:
            encrypted = self._get_fernet().encrypt(data)
            return encrypted.decode('utf-8')
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {str(e)}")
    
    def decrypt(self, encrypted_data: Union[str, bytes, None]) -> Optional[str]:
        """
        Decrypt previously encrypted data.
        
        Args:
            encrypted_data: Base64-encoded encrypted string.
            
        Returns:
            Decrypted string, or None if input was None.
            
        Raises:
            EncryptionError: If decryption fails (e.g., wrong key, corrupted data).
        """
        if encrypted_data is None:
            return None
        
        try:
            if isinstance(encrypted_data, str):
                encrypted_data = encrypted_data.encode('utf-8')
            
            decrypted = self._get_fernet().decrypt(encrypted_data)
            return decrypted.decode('utf-8')
        except InvalidToken:
            raise EncryptionError("Decryption failed: Invalid token or wrong key")
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {str(e)}")
    
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
            os.environ['CMS_ENCRYPTION_KEY'] = new_key
            self._fernet = None  # Reset Fernet instance
            self._get_fernet()  # Validate new key
            return True
        except Exception:
            return False
    
    @classmethod
    def reset(cls):
        """Reset singleton instance (useful for testing)."""
        cls._instance = None
        cls._fernet = None


# Global instance for convenience
encryptor = FieldEncryptor()


def encrypt_field(data: Union[str, bytes, None]) -> Optional[str]:
    """Convenience function for encrypting a single field."""
    return encryptor.encrypt(data)


def decrypt_field(data: Union[str, bytes, None]) -> Optional[str]:
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
        from sqlalchemy.dialects.postgresql import BYTEA
        
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
