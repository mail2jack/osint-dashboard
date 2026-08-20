"""
PR59: Encryption integrity regression tests.

Covers:
  1. decrypt_identifiers() tracks failures in _decryption_errors
  2. Corrupt ciphertext is detected, not silently ignored
  3. Key rotation scenario: old-key fields are flagged, not silently dropped
  4. has_decryption_errors property works
  5. decrypt_identifiers is idempotent (no double-encrypt)
"""

from cms.models import db, Subject
from cms.encryption_utils import encryptor


def _make_subject(tenant_id=None):
    """Create a minimal subject for testing."""
    s = Subject(
        name="Encrypt Test Subject",
        subject_type="person",
        tenant_id=tenant_id or "00000000-0000-0000-0000-000000000001",
    )
    db.session.add(s)
    db.session.flush()
    return s


class TestDecryptFailureTracking:
    """decrypt_identifiers must track and report failures, not silently swallow them."""

    def test_no_errors_when_all_fields_empty(self, db_session):
        s = _make_subject()
        s.decrypt_identifiers()
        assert s.has_decryption_errors is False
        assert s.decryption_errors == []

    def test_no_errors_when_all_fields_decrypt(self, db_session):
        s = _make_subject()
        s.email = encryptor.encrypt("test@example.com")
        s.phone = encryptor.encrypt("+31612345678")
        s.city = encryptor.encrypt("Amsterdam")
        db.session.flush()

        s.decrypt_identifiers()
        assert s.has_decryption_errors is False
        assert s.email == "test@example.com"
        assert s.phone == "+31612345678"
        assert s.city == "Amsterdam"

    def test_errors_tracked_for_corrupt_ciphertext(self, db_session):
        s = _make_subject()
        db.session.flush()

        # Insert raw ciphertext directly to bypass before_flush re-encryption
        from sqlalchemy import text

        db.session.execute(
            text("UPDATE subjects SET email = :val, city = :val2 WHERE id = :id"),
            {
                "val": "this-is-not-valid-ciphertext",
                "val2": "also-not-valid",
                "id": s.id,
            },
        )
        db.session.expire(s)

        s.decrypt_identifiers()
        assert s.has_decryption_errors is True
        assert len(s.decryption_errors) == 2

        fields_failed = {e["field"] for e in s.decryption_errors}
        assert "email" in fields_failed
        assert "city" in fields_failed
        assert all(e["error"] == "EncryptionError" for e in s.decryption_errors)

    def test_errors_tracked_for_fernet_prefix_but_wrong_key(self, db_session):
        """Simulate key rotation: encrypt with one key, then swap key."""
        s = _make_subject()
        s.city = encryptor.encrypt("Rotterdam")
        db.session.flush()

        # Swap to a different key
        original_key = encryptor._get_key()
        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key().decode()
        try:
            encryptor.rotate_key(new_key)
            s._decryption_errors = []
            s.decrypt_identifiers()
            assert s.has_decryption_errors is True
            assert len(s.decryption_errors) == 1
            assert s.decryption_errors[0]["field"] == "city"
            assert "EncryptionError" in s.decryption_errors[0]["error"]
        finally:
            encryptor.rotate_key(original_key)

    def test_decryption_errors_reset_on_each_call(self, db_session):
        s = _make_subject()
        db.session.flush()

        # Insert raw corrupt ciphertext directly
        from sqlalchemy import text

        db.session.execute(
            text("UPDATE subjects SET email = :val WHERE id = :id"),
            {"val": "corrupt-data-here", "id": s.id},
        )
        db.session.expire(s)

        # First call: errors
        s.decrypt_identifiers()
        assert s.has_decryption_errors is True

        # Fix the field and call again: errors should reset
        s.email = encryptor.encrypt("fixed@example.com")
        db.session.flush()
        s.decrypt_identifiers()
        assert s.has_decryption_errors is False

    def test_idempotent_no_double_encrypt(self, db_session):
        """Calling decrypt_identifiers twice should not double-encrypt."""
        s = _make_subject()
        s.city = encryptor.encrypt("Utrecht")
        db.session.flush()

        s.decrypt_identifiers()
        assert s.city == "Utrecht"

        s.decrypt_identifiers()
        assert s.city == "Utrecht"

    def test_has_decryption_errors_property_default(self, db_session):
        """Before any decrypt call, has_decryption_errors should be False."""
        s = _make_subject()
        assert s.has_decryption_errors is False
        assert s.decryption_errors == []


class TestKeyRotationScenario:
    """Test the exact scenario that caused the production bug."""

    def test_mix_of_old_and_new_key_fields(self, db_session):
        """Some fields encrypted with key A, others with key B."""
        s = _make_subject()
        s.email = encryptor.encrypt("good@example.com")
        s.city = encryptor.encrypt("Amsterdam")
        db.session.flush()

        # Simulate key rotation
        original_key = encryptor._get_key()
        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key().decode()
        try:
            encryptor.rotate_key(new_key)
            # Now encrypt phone with the NEW key
            s.phone = encryptor.encrypt("+31699999999")
            db.session.flush()

            s._decryption_errors = []
            s.decrypt_identifiers()

            # email and city (old key) should fail
            assert s.has_decryption_errors is True
            failed_fields = {e["field"] for e in s.decryption_errors}
            assert "email" in failed_fields
            assert "city" in failed_fields

            # phone (new key) should succeed
            assert s.phone == "+31699999999"
        finally:
            encryptor.rotate_key(original_key)
