"""Queue-contract tests for workflow-native passive source research."""

import uuid

import pytest

from cms.models import Case, Investigation, SpiderFootScan, User, db
from cms.services.workflow_source_research import (
    SourceResearchRejected,
    TARGET_TYPES,
    passive_profile_for,
    queue_passive_source_research,
    validate_target,
)
from cms.workflow.models import WorkflowResearchAction


def _admin():
    return User.query.filter_by(username="admin").first()


def _case_with_open_investigation(auth_client):
    token = uuid.uuid4().hex[:8]
    title = f"Passive source research {token}"
    response = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": f"Source Research Client {token}",
            "title": title,
            "subject_0_name": "Source Research Subject",
            "subject_0_type": "person",
            "priority": "medium",
        },
    )
    assert response.status_code in (200, 302)
    case = Case.query.filter_by(title=title).one()
    investigation = Investigation(
        tenant_id=case.tenant_id,
        case_id=case.id,
        title="Passive research investigation",
        sequence_no=1,
        status="open",
    )
    db.session.add(investigation)
    db.session.commit()
    return case, investigation, case.subjects.first()


def test_target_contract_accepts_supported_target_types_and_passive_profiles():
    assert set(TARGET_TYPES) >= {
        "person",
        "email",
        "domain",
        "ip",
        "url",
        "company",
    }
    assert passive_profile_for("person") == "investigation"
    assert passive_profile_for("domain") == "company"
    assert passive_profile_for("ip") == "threat_hunt"
    assert validate_target("email", "  analyst@example.test ") == (
        "email",
        "analyst@example.test",
    )


@pytest.mark.parametrize(
    "target_type,target_value",
    [
        ("unknown", "example.test"),
        ("domain", ""),
        ("domain", "a" * 501),
        ("domain", "example.test\nnext"),
        ("domain", 42),
    ],
)
def test_target_contract_rejects_invalid_input(target_type, target_value):
    with pytest.raises(SourceResearchRejected):
        validate_target(target_type, target_value)


def test_queue_creates_action_and_linked_passive_scan(auth_client):
    case, investigation, subject = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="username",
        target_value="  research_user  ",
        subject=subject,
    )
    db.session.commit()

    persisted_action = db.session.get(WorkflowResearchAction, action.id)
    persisted_scan = db.session.get(SpiderFootScan, scan.id)
    assert persisted_action is not None
    assert persisted_scan is not None
    assert persisted_action.status == "pending"
    assert persisted_action.investigation_id == investigation.id
    assert persisted_action.subject_id == subject.id
    assert persisted_action.action_type == "source_research"
    assert persisted_scan.research_action_id == persisted_action.id
    assert persisted_scan.investigation_id == investigation.id
    assert persisted_scan.scan_id == f"queued:{persisted_action.id}"
    assert persisted_scan.use_case == "passive"
    assert persisted_scan.status == "pending"


def test_queue_rejects_cross_case_investigation_without_writes(auth_client):
    case_a, _, _ = _case_with_open_investigation(auth_client)
    case_b, investigation_b, _ = _case_with_open_investigation(auth_client)
    before_actions = WorkflowResearchAction.query.count()
    before_scans = SpiderFootScan.query.count()

    with pytest.raises(SourceResearchRejected, match="does not belong"):
        queue_passive_source_research(
            case=case_a,
            investigation=investigation_b,
            actor=_admin(),
            target_type="domain",
            target_value="example.test",
        )

    assert WorkflowResearchAction.query.count() == before_actions
    assert SpiderFootScan.query.count() == before_scans
    assert case_a.id != case_b.id
