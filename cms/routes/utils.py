"""
Utility functions for CMS routes.
"""

import re
import logging
from typing import Optional

from ..models import db, Subject, Client

logger = logging.getLogger(__name__)


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
    normalized = re.sub(r'\s+', ' ', normalized)  # Multiple spaces to single
    # Remove common prefixes
    for prefix in ['mr.', 'mrs.', 'ms.', 'dr.', 'ing.', 'ir.']:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
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

    # Filter by first letter in DB to avoid loading all records
    first_letter = normalized_input[0]
    candidates = Subject.query.filter(
        Subject.is_deleted == False,
        Subject.name.ilike(f'{first_letter}%')
    ).limit(500).all()

    for subject in candidates:
        if normalize_name(subject.name) == normalized_input:
            continue

        similarity = calculate_similarity(name, subject.name)
        if similarity >= threshold:
            similar.append({
                'id': subject.id,
                'name': subject.name,
                'type': subject.subject_type,
                'similarity': round(similarity * 100)
            })

    return sorted(similar, key=lambda x: x['similarity'], reverse=True)[:10]


def find_similar_clients(name: str, threshold: float = 0.7) -> list:
    """Find clients with similar names."""
    if not name or len(name) < 2:
        return []

    normalized_input = normalize_name(name)
    similar = []

    first_letter = normalized_input[0]
    candidates = Client.query.filter(
        Client.is_deleted == False,
        Client.is_active == True,
        Client.name.ilike(f'{first_letter}%')
    ).limit(500).all()

    for client in candidates:
        if normalize_name(client.name) == normalized_input:
            continue

        similarity = calculate_similarity(name, client.name)
        if similarity >= threshold:
            similar.append({
                'id': client.id,
                'name': client.name,
                'similarity': round(similarity * 100)
            })

    return sorted(similar, key=lambda x: x['similarity'], reverse=True)[:10]


def check_for_exact_match(name: str, entity_type: str) -> Optional[dict]:
    """Check for exact or very close match."""
    if not name:
        return None

    normalized = normalize_name(name)

    if entity_type == 'subject':
        subject = Subject.query.filter(
            Subject.is_deleted == False,
            Subject.name.ilike(normalized)
        ).first()
        if subject:
            return {
                'id': subject.id,
                'name': subject.name,
                'type': subject.subject_type,
                'exact': normalize_name(subject.name) == normalized
            }
    elif entity_type == 'client':
        client = Client.query.filter(
            Client.is_deleted == False,
            Client.is_active == True,
            Client.name.ilike(normalized)
        ).first()
        if client:
            return {
                'id': client.id,
                'name': client.name,
                'exact': normalize_name(client.name) == normalized
            }

    return None


def normalize_phone(phone) -> str | None:
    """Normalize any phone number format to E164 (+31634407404)."""
    if not phone:
        return phone
    phone = phone.strip()
    if HAS_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(phone, 'NL')
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except Exception:
            logger.debug("Phone number normalization failed for %s", phone)
    # Fallback: strip all non-digits, prepend +
    digits = re.sub(r'[^0-9]', '', phone)
    if digits.startswith('0'):
        digits = '31' + digits[1:]  # assume NL
    return '+' + digits
