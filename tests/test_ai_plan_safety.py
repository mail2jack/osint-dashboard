"""Regression coverage for the human-in-the-loop AI research boundary."""

from cms.workflow.ai_plan_routes import _build_plan


def test_ai_plan_only_proposes_allowlisted_non_paid_actions():
    plan = _build_plan(
        "Controleer het e-mailadres",
        {"type": "email", "query": "person@example.nl", "confidence": 0.9, "source": "test"},
    )

    assert plan["policy"]["execution"] == "approval_required"
    assert plan["policy"]["automatic_verification"] is False
    assert plan["policy"]["paid_channels"] == "not_proposed"
    assert plan["actions"]
    assert all(action["action_type"] in {"email", "google_dork", "browser_search"} for action in plan["actions"])


def test_ai_plan_relationships_are_suggestions_not_facts():
    plan = _build_plan(
        "Onderzoek de naam",
        {"type": "name", "query": "Jan Janssen", "confidence": 0.35, "source": "offline-rules"},
    )

    assert plan["relationship_expansion"]["status"] == "suggested"
    assert "do not treat suggestions as facts" in plan["relationship_expansion"]["rule"]
