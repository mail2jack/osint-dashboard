"""Conservative, explainable entity-resolution suggestions.

This module deliberately returns suggestions only. It never changes a
subject, links a subject to a case, or promotes a candidate to a fact.
"""

import re

from cms.routes.utils import calculate_similarity, normalize_name, normalize_phone


def _clean(value):
    return str(value or "").strip()


def _norm_email(value):
    return _clean(value).casefold()


def _norm_username(value):
    return re.sub(r"^@", "", _clean(value)).casefold()


def _norm_phone(value):
    value = _clean(value)
    return normalize_phone(value) if value else ""


def _values(candidate, key):
    values = candidate.get(key) or []
    if isinstance(values, str):
        values = [values]
    return [_clean(value) for value in values if _clean(value)]


def score_entity_candidate(observed, candidate):
    """Score one candidate and explain every positive or negative signal."""
    observed = observed or {}
    candidate = candidate or {}
    reasons = []
    warnings = []
    score = 0.0
    exact_identifier_matches = 0

    observed_email = _norm_email(observed.get("email"))
    candidate_emails = {_norm_email(value) for value in _values(candidate, "emails")}
    if observed_email and observed_email in candidate_emails:
        score += 0.85
        exact_identifier_matches += 1
        reasons.append("exact e-mailadres")

    observed_phone = _norm_phone(observed.get("phone"))
    candidate_phones = {_norm_phone(value) for value in _values(candidate, "phones")}
    if observed_phone and observed_phone in candidate_phones:
        score += 0.85
        exact_identifier_matches += 1
        reasons.append("exact telefoonnummer")

    observed_username = _norm_username(observed.get("username"))
    candidate_usernames = {_norm_username(value) for value in _values(candidate, "usernames")}
    if observed_username and observed_username in candidate_usernames:
        score += 0.85
        exact_identifier_matches += 1
        reasons.append("exact gebruikersnaam")

    observed_name = _clean(observed.get("name"))
    candidate_name = _clean(candidate.get("name"))
    name_similarity = calculate_similarity(observed_name, candidate_name)
    if name_similarity:
        score += min(name_similarity * 0.15, 0.15)
        if name_similarity >= 0.99:
            reasons.append("exacte naam")
        elif name_similarity >= 0.70:
            reasons.append(f"vergelijkbare naam ({round(name_similarity * 100)}%)")
            warnings.append("naamovereenkomst is op zichzelf geen bewijs")

    if exact_identifier_matches and observed_name and candidate_name:
        if normalize_name(observed_name) != normalize_name(candidate_name):
            warnings.append("identifier komt overeen, maar de naam verschilt")
            score -= 0.15

    score = max(0.0, min(score, 1.0))
    if exact_identifier_matches and score >= 0.75:
        confidence = "high"
    elif score >= 0.65:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "subject_id": candidate.get("id"),
        "subject_name": candidate_name,
        "subject_type": candidate.get("subject_type"),
        "score": round(score, 3),
        "confidence": confidence,
        "reasons": reasons,
        "warnings": warnings,
        "requires_human_confirmation": True,
        "automatic_link": False,
    }


def suggest_entity_matches(observed, candidates, *, minimum_score=0.55, limit=10):
    """Return explainable candidate matches without performing a write."""
    results = [score_entity_candidate(observed, candidate) for candidate in candidates or []]
    results = [result for result in results if result["score"] >= minimum_score]
    results.sort(key=lambda result: (result["score"], result["confidence"]), reverse=True)
    return results[:limit]
