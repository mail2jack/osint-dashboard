from types import SimpleNamespace

from cms.services.finding_followups import extract_finding_followups


def test_extracts_accounts_contacts_and_subject_relations_from_finding_text():
    finding = SimpleNamespace(
        id="finding-1",
        title="LinkedIn and contact details",
        content=(
            "Foivi Melina Nearchou and Ivan Versteegh are mentioned together. "
            "Profile: https://www.linkedin.com/in/foivi-melina-nearchou-325ab2256/ "
            "Phone +31 6 12345678."
        ),
        detail="Email: foivi@example.com",
        source_url="https://example.test/source",
    )
    foivi = SimpleNamespace(id="subject-1", name="Foivi Melina Nearchou")
    ivan = SimpleNamespace(id="subject-2", name="Ivan Versteegh")

    result = extract_finding_followups([finding], [foivi, ivan])

    assert result["emails"][0]["value"] == "foivi@example.com"
    assert result["phones"][0]["value"] == "+31612345678"
    assert result["accounts"][0]["platform"] == "linkedin"
    assert result["relations"][0]["subject_ids"] == ["subject-1", "subject-2"]
