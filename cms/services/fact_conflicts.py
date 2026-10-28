"""Conservative conflict detection for structured subject facts."""

from __future__ import annotations

from collections import defaultdict


def _fact_value(fact):
    getter = getattr(fact, "get_value", None)
    if callable(getter):
        return getter()
    if isinstance(fact, dict):
        return fact.get("value")
    return getattr(fact, "value", None)


def detect_fact_conflicts(facts) -> list[dict]:
    """Return conflicts only for identical keys with multiple active values."""
    grouped = defaultdict(list)
    for fact in facts or []:
        status = (fact.get("status") if isinstance(fact, dict) else getattr(fact, "status", None)) or "candidate"
        if status in {"rejected", "superseded"}:
            continue
        key = (fact.get("fact_key") if isinstance(fact, dict) else getattr(fact, "fact_key", None)) or ""
        value = _fact_value(fact)
        normalized = " ".join(str(value or "").casefold().split())
        if key and normalized:
            fact_id = fact.get("id") if isinstance(fact, dict) else getattr(fact, "id", None)
            grouped[key].append((normalized, fact_id))

    conflicts = []
    for fact_key, rows in grouped.items():
        values = {value for value, _ in rows}
        if len(values) > 1:
            conflicts.append({
                "fact_key": fact_key,
                "value_count": len(values),
                "fact_ids": [fact_id for _, fact_id in rows if fact_id],
            })
    return sorted(conflicts, key=lambda item: item["fact_key"].casefold())
