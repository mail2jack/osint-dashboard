"""Tests for encryption key rotation support."""
import base64
import os
import secrets

import pytest
from cryptography.fernet import Fernet

from cms.encryption_utils import EncryptionError, FieldEncryptor


@pytest.fixture()
def new_key():
    """Generate a fresh Fernet key for rotation tests."""
    return Fernet.generate_key().decode()


@pytest.fixture()
def encryptor():
    """Create a fresh FieldEncryptor for each test."""
    FieldEncryptor.reset()
    enc = FieldEncryptor()
    yield enc
    FieldEncryptor.reset()


class TestMultiKeyDecrypt:
    """Decrypt with legacy keys when current key has changed."""

    def test_decrypt_with_current_key(self, encryptor):
        plaintext = "secret BSN 123456789"
        encrypted = encryptor.encrypt(plaintext)
        assert encryptor.decrypt(encrypted) == plaintext

    def test_decrypt_with_legacy_key(self, encryptor):
        """Data encrypted with old key can be decrypted after key change."""
        old_key = os.environ.get("CMS_ENCRYPTION_KEY")
        plaintext = "secret data"

        # Encrypt with current key
        old_encrypted = encryptor.encrypt(plaintext)

        # Rotate to new key
        new_key = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = new_key
        os.environ["CMS_ENCRYPTION_KEYS"] = old_key
        FieldEncryptor.reset()
        enc = FieldEncryptor()

        # Should still decrypt with legacy key
        assert enc.decrypt(old_encrypted) == plaintext

        # Cleanup
        os.environ["CMS_ENCRYPTION_KEYS"] = ""

    def test_decrypt_fails_with_wrong_key(self, encryptor):
        """Cannot decrypt with completely wrong key."""
        plaintext = "secret"
        encrypted = encryptor.encrypt(plaintext)

        new_key = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = new_key
        os.environ["CMS_ENCRYPTION_KEYS"] = ""
        FieldEncryptor.reset()
        enc = FieldEncryptor()

        with pytest.raises(EncryptionError, match="wrong key"):
            enc.decrypt(encrypted)

        # Cleanup
        os.environ["CMS_ENCRYPTION_KEY"] = new_key

    def test_multiple_legacy_keys(self, encryptor):
        """Multiple legacy keys are all tried."""
        key1 = os.environ.get("CMS_ENCRYPTION_KEY")

        # Encrypt with key1
        data1 = encryptor.encrypt("encrypted with key1")

        # Rotate to key2
        key2 = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = key2
        os.environ["CMS_ENCRYPTION_KEYS"] = key1
        FieldEncryptor.reset()
        enc2 = FieldEncryptor()
        data2 = enc2.encrypt("encrypted with key2")

        # Rotate to key3
        key3 = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = key3
        os.environ["CMS_ENCRYPTION_KEYS"] = f"{key1},{key2}"
        FieldEncryptor.reset()
        enc3 = FieldEncryptor()

        # Both old encryptions should still decrypt
        assert enc3.decrypt(data1) == "encrypted with key1"
        assert enc3.decrypt(data2) == "encrypted with key2"

        # Cleanup
        os.environ["CMS_ENCRYPTION_KEYS"] = ""


class TestReEncrypt:
    """Re-encryption of data from legacy key to current key."""

    def test_re_encrypt_same_key(self, encryptor):
        """Re-encrypt with same key produces different ciphertext but same plaintext."""
        plaintext = "test value"
        encrypted = encryptor.encrypt(plaintext)
        re_encrypted = encryptor.re_encrypt(encrypted)
        # Fernet includes a timestamp, so ciphertext differs each time
        assert re_encrypted != encrypted
        assert encryptor.decrypt(re_encrypted) == plaintext

    def test_re_encrypt_with_legacy_key(self, encryptor):
        """Re-encrypt moves data from legacy key to current key."""
        old_key = os.environ.get("CMS_ENCRYPTION_KEY")
        plaintext = "sensitive data"

        # Encrypt with old key
        old_encrypted = encryptor.encrypt(plaintext)

        # Rotate to new key
        new_key = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = new_key
        os.environ["CMS_ENCRYPTION_KEYS"] = old_key
        FieldEncryptor.reset()
        enc = FieldEncryptor()

        # Re-encrypt should use new key
        re_encrypted = enc.re_encrypt(old_encrypted)
        assert re_encrypted is not None
        assert re_encrypted != old_encrypted

        # Should decrypt with current key (no legacy needed)
        assert enc.decrypt(re_encrypted) == plaintext
        assert enc.is_encrypted_with_current_key(re_encrypted)

        # Cleanup
        os.environ["CMS_ENCRYPTION_KEYS"] = ""

    def test_re_encrypt_none(self, encryptor):
        """Re-encrypt None returns None."""
        assert encryptor.re_encrypt(None) is None

    def test_is_encrypted_with_current_key(self, encryptor):
        """Check which key encrypted the data."""
        plaintext = "check me"
        encrypted = encryptor.encrypt(plaintext)
        assert encryptor.is_encrypted_with_current_key(encrypted) is True

        old_key = os.environ.get("CMS_ENCRYPTION_KEY")
        new_key = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEY"] = new_key
        os.environ["CMS_ENCRYPTION_KEYS"] = old_key
        FieldEncryptor.reset()
        enc = FieldEncryptor()

        assert enc.is_encrypted_with_current_key(encrypted) is False

        # Cleanup
        os.environ["CMS_ENCRYPTION_KEYS"] = ""


class TestLegacyKeyParsing:
    """CMS_ENCRYPTION_KEYS parsing edge cases."""

    def test_empty_env(self, encryptor):
        assert encryptor._get_legacy_keys() == []

    def test_single_key(self, encryptor):
        key = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEYS"] = key
        FieldEncryptor.reset()
        enc = FieldEncryptor()
        assert len(enc._get_legacy_keys()) == 1
        os.environ["CMS_ENCRYPTION_KEYS"] = ""

    def test_multiple_keys(self, encryptor):
        k1 = Fernet.generate_key().decode()
        k2 = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEYS"] = f"{k1},{k2}"
        FieldEncryptor.reset()
        enc = FieldEncryptor()
        assert len(enc._get_legacy_keys()) == 2
        os.environ["CMS_ENCRYPTION_KEYS"] = ""

    def test_invalid_key_skipped(self, encryptor):
        valid = Fernet.generate_key().decode()
        os.environ["CMS_ENCRYPTION_KEYS"] = f"not-a-valid-key,{valid}"
        FieldEncryptor.reset()
        enc = FieldEncryptor()
        assert len(enc._get_legacy_keys()) == 1
        os.environ["CMS_ENCRYPTION_KEYS"] = ""
