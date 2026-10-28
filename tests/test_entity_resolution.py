"""Safety and explainability tests for subject-resolution suggestions."""

from cms.services.entity_resolution import score_entity_candidate, suggest_entity_matches


def test_exact_identifier_is_high_confidence_but_never_automatic():
    result = score_entity_candidate(
        {"name": "Jan Janssen", "email": "person@example.nl"},
        {"id": "subject-1", "name": "Jan Janssen", "subject_type": "person", "emails": ["person@example.nl"]},
    )
    assert result["confidence"] == "high"
    assert "exact e-mailadres" in result["reasons"]
    assert result["requires_human_confirmation"] is True
    assert result["automatic_link"] is False


def test_name_only_match_is_not_high_confidence():
    result = score_entity_candidate(
        {"name": "Jan Jansen"},
        {"id": "subject-2", "name": "Jan Janssen", "subject_type": "person"},
    )
    assert result["confidence"] != "high"
    assert "naamovereenkomst is op zichzelf geen bewijs" in result["warnings"]


def test_conflicting_identifier_is_explained():
    result = score_entity_candidate(
        {"name": "Jan Janssen", "phone": "+31612345678"},
        {"id": "subject-3", "name": "Piet de Vries", "subject_type": "person", "phones": ["+31612345678"]},
    )
    assert result["automatic_link"] is False
    assert "identifier komt overeen, maar de naam verschilt" in result["warnings"]


def test_suggestions_are_sorted_and_filtered():
    results = suggest_entity_matches(
        {"email": "person@example.nl"},
        [
            {"id": "weak", "name": "Other", "emails": ["other@example.nl"]},
            {"id": "strong", "name": "Person", "emails": ["person@example.nl"]},
        ],
    )
    assert [result["subject_id"] for result in results] == ["strong"]
    assert all(result["requires_human_confirmation"] for result in results)
