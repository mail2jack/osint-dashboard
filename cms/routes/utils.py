"""
Utility functions for CMS routes.
"""

import re
import logging

from flask_login import current_user

from ..models import Subject, Client

logger = logging.getLogger(__name__)


def is_safe_url(url_str: str) -> bool:
    """Validate a URL to prevent SSRF attacks: block private/loopback IPs."""
    from cms.services.ssrf_guard import validate_url

    return validate_url(url_str)[0]


try:
    import phonenumbers

    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False


def normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    if not name:
        return ""
    # Lowercase, remove extra spaces, remove common prefixes/suffixes
    normalized = name.lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)  # Multiple spaces to single
    # Remove common prefixes
    for prefix in ["mr.", "mrs.", "ms.", "dr.", "ing.", "ir."]:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    return normalized


def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate similarity between two strings (0-1)."""
    s1, s2 = normalize_name(s1), normalize_name(s2)
    if not s1 or not s2:
        return 0.0

    # Exact match
    if s1 == s2:
        return 1.0

    # Quick checks
    if s1 in s2 or s2 in s1:
        return 0.85

    # Levenshtein-based similarity
    len_sum = len(s1) + len(s2)
    if len_sum == 0:
        return 0.0

    # Simple character-based similarity
    common = sum(1 for a, b in zip(s1, s2) if a == b)
    return common * 2 / len_sum


def find_similar_subjects(name: str, threshold: float = 0.7) -> list:
    """Find subjects with similar names."""
    if not name or len(name) < 2:
        return []

    normalized_input = normalize_name(name)
    similar = []

    # Load all subjects in tenant and decrypt names, then filter in Python.
    # Encrypted names cannot be searched with SQL ILIKE.
    candidates = (
        Subject.query.filter(
            Subject.is_deleted == False,
            Subject.tenant_id == current_user.tenant_id,
        )
        .limit(500)
        .all()
    )

    for subject in candidates:
        subject.decrypt_identifiers()
        if normalize_name(subject.name) == normalized_input:
            continue

        similarity = calculate_similarity(name, subject.name)
        if similarity >= threshold:
            similar.append(
                {
                    "id": subject.id,
                    "name": subject.name,
                    "type": subject.subject_type,
                    "similarity": round(similarity * 100),
                }
            )

    return sorted(similar, key=lambda x: x["similarity"], reverse=True)[:10]


def find_similar_clients(name: str, threshold: float = 0.7) -> list:
    """Find clients with similar names."""
    if not name or len(name) < 2:
        return []

    normalized_input = normalize_name(name)
    similar = []

    first_letter = normalized_input[0]
    candidates = (
        Client.query.filter(
            Client.is_deleted == False,
            Client.is_active == True,
            Client.tenant_id == current_user.tenant_id,
            Client.name.ilike(f"{first_letter}%"),
        )
        .limit(500)
        .all()
    )

    for client in candidates:
        if normalize_name(client.name) == normalized_input:
            continue

        similarity = calculate_similarity(name, client.name)
        if similarity >= threshold:
            similar.append(
                {
                    "id": client.id,
                    "name": client.name,
                    "similarity": round(similarity * 100),
                }
            )

    return sorted(similar, key=lambda x: x["similarity"], reverse=True)[:10]


def check_for_exact_match(name: str, entity_type: str) -> dict | None:
    """Check for exact or very close match."""
    if not name:
        return None

    normalized = normalize_name(name)

    if entity_type == "subject":
        # Encrypted names cannot be searched with SQL ILIKE.
        # Load all subjects in tenant and decrypt to find exact match.
        subjects = Subject.query.filter(
            Subject.is_deleted == False,
            Subject.tenant_id == current_user.tenant_id,
        ).all()
        for subject in subjects:
            subject.decrypt_identifiers()
            if normalize_name(subject.name) == normalized:
                return {
                    "id": subject.id,
                    "name": subject.name,
                    "type": subject.subject_type,
                    "exact": True,
                }
        return None
    elif entity_type == "client":
        client = Client.query.filter(
            Client.is_deleted == False,
            Client.is_active == True,
            Client.tenant_id == current_user.tenant_id,
            Client.name.ilike(normalized),
        ).first()
        if client:
            return {
                "id": client.id,
                "name": client.name,
                "exact": normalize_name(client.name) == normalized,
            }

    return None


def normalize_phone(phone) -> str | None:
    """Normalize any phone number format to E164 (+31634407404)."""
    if not phone:
        return phone
    phone = phone.strip()
    if HAS_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(phone, "NL")
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except Exception:
            logger.debug("Phone number normalization failed for %s", phone)
    # Fallback: strip all non-digits, prepend +
    digits = re.sub(r"[^0-9]", "", phone)
    if digits.startswith("0"):
        digits = "31" + digits[1:]  # assume NL
    return "+" + digits


def normalize_postcode(postcode: str) -> str:
    """Normalize Dutch postcode to 4 digits + 2 uppercase letters (e.g. 6537GN)."""
    if not postcode:
        return postcode
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", postcode.strip())
    if len(cleaned) == 6:
        digits = cleaned[:4]
        letters = cleaned[4:].upper()
        if digits.isdigit() and letters.isalpha():
            return digits + letters
    return postcode.strip()
