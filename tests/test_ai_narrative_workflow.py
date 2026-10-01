"""Regression coverage for the source-bound AI investigation narrative."""

from datetime import date
from unittest.mock import patch
import uuid

from cms.models import (
    ActionFinding,
    Case,
    Client,
    Finding,
    Investigation,
    ResearchAction,
    User,
    db,
)


def _fixture_graph():
    user = User.query.filter_by(username="admin").first()
    client = Client(
        tenant_id=user.tenant_id,
        name="AI narrative regression client",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()

    case = Case(
        tenant_id=user.tenant_id,
        case_number=f"AI-{uuid.uuid4().hex[:10]}",
        client_id=client.id,
        title="AI narrative regression case",
        status="open",
        priority="medium",
        start_date=date.today(),
        created_by=user.id,
    )
    db.session.add(case)
    db.session.flush()

    investigation = Investigation(
        tenant_id=user.tenant_id,
        case_id=case.id,
        sequence_no=1,
        title="AI narrative regression investigation",
        created_by=user.id,
    )
    db.session.add(investigation)
    db.session.flush()

    action = ResearchAction(
        tenant_id=user.tenant_id,
        case_id=case.id,
        investigation_id=investigation.id,
        action_type="manual_entry",
        label="Regression action",
        status="completed",
        created_by=user.id,
    )
    finding = Finding(
        tenant_id=user.tenant_id,
        case_id=case.id,
        title="Verified regression finding",
        content="A source-bound finding used by the narrative test.",
        source_type="manual",
        source_url="https://example.test/source",
        status="verified",
        verified=True,
        include_in_report=True,
        created_by=user.id,
    )
    db.session.add_all([action, finding])
    db.session.flush()
    db.session.add(ActionFinding(action_id=action.id, finding_id=finding.id))
    db.session.commit()
    return case, investigation, finding


def test_narrative_is_stored_and_rendered_in_report(auth_client, app):
    with app.app_context():
        case, investigation, finding = _fixture_graph()
        narrative = "Kernbeeld: de bron ondersteunt deze gecontroleerde bevinding."

        with patch("cms.workflow.ai_plan_routes._generate", return_value=narrative):
            response = auth_client.post(
                f"/cms/workflow/api/case/{case.id}/investigations/"
                f"{investigation.id}/ai-research/narrative",
                json={},
            )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["stored"] is True
        assert payload["finding_count"] == 1

        stored = db.session.get(Investigation, investigation.id)
        assert stored.ai_narrative == narrative
        assert stored.ai_narrative_generated_by == User.query.filter_by(
            username="admin"
        ).first().id
        assert stored.ai_narrative_finding_count == 1

        report = auth_client.get(f"/cms/workflow/case/{case.id}/pv")
        assert report.status_code == 200
        assert narrative.encode() in report.data
        assert b"interpretatieve samenvatting" in report.data
        assert finding.title.encode() in report.data
