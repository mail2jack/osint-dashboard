"""Queue-contract tests for workflow-native passive source research."""

import uuid

import pytest

from cms.models import AuditLog, Case, FeatureFlag, Investigation, SpiderFootScan, User, db
from cms.services.workflow_source_research import (
    SourceResearchRejected,
    TARGET_TYPES,
    passive_profile_for,
    queue_passive_source_research,
    validate_target,
)
from cms.services import workflow_source_research_worker as source_worker
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


def _enable_workflow_source_research():
    tenant_id = _admin().tenant_id
    flag = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name="workflow_spiderfoot"
    ).first()
    if flag is None:
        flag = FeatureFlag(
            tenant_id=tenant_id, flag_name="workflow_spiderfoot", enabled=True
        )
        db.session.add(flag)
    else:
        flag.enabled = True
    db.session.commit()


def _request_url(case_id, investigation_id):
    return (
        f"/cms/workflow/api/case/{case_id}/investigations/"
        f"{investigation_id}/source-research"
    )


def _status_url(case_id, investigation_id, action_id):
    return f"{_request_url(case_id, investigation_id)}/{action_id}"


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


def test_route_is_hidden_until_explicit_feature_enablement(auth_client):
    case, investigation, _ = _case_with_open_investigation(auth_client)
    response = auth_client.post(
        _request_url(case.id, investigation.id),
        json={"target_type": "domain", "target_value": "example.test"},
    )
    assert response.status_code == 404


def test_route_queues_scoped_passive_research_with_audit(auth_client):
    _enable_workflow_source_research()
    case, investigation, subject = _case_with_open_investigation(auth_client)

    response = auth_client.post(
        _request_url(case.id, investigation.id),
        json={
            "target_type": "email",
            "target_value": "analyst@example.test",
            "subject_id": subject.id,
        },
    )
    assert response.status_code == 202
    body = response.get_json()
    action = db.session.get(WorkflowResearchAction, body["action"]["id"])
    scan = db.session.get(SpiderFootScan, body["scan"]["id"])
    assert action is not None and scan is not None
    assert action.investigation_id == investigation.id
    assert scan.research_action_id == action.id
    assert scan.use_case == "passive"
    assert scan.status == action.status == "pending"
    audits = AuditLog.query.filter(
        AuditLog.entity_id.in_([action.id, scan.id])
    ).all()
    assert {entry.entity_type for entry in audits} == {
        "research_action",
        "spiderfoot_scan",
    }


def test_route_rejects_unknown_field_without_partial_records(auth_client):
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    before_actions = WorkflowResearchAction.query.count()
    before_scans = SpiderFootScan.query.count()
    response = auth_client.post(
        _request_url(case.id, investigation.id),
        json={
            "target_type": "domain",
            "target_value": "example.test",
            "use_case": "all",
        },
    )
    assert response.status_code == 400
    assert WorkflowResearchAction.query.count() == before_actions
    assert SpiderFootScan.query.count() == before_scans


def test_route_rejects_archived_investigation_without_partial_records(auth_client):
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    investigation.status = "archived"
    db.session.commit()
    before_actions = WorkflowResearchAction.query.count()
    before_scans = SpiderFootScan.query.count()
    response = auth_client.post(
        _request_url(case.id, investigation.id),
        json={"target_type": "domain", "target_value": "example.test"},
    )
    assert response.status_code == 409
    assert WorkflowResearchAction.query.count() == before_actions
    assert SpiderFootScan.query.count() == before_scans


class _FakeSpiderFoot:
    def __init__(self, *, status=None):
        self.status = status or {"status": "finished", "progress": 100}
        self.started = []

    def is_available(self):
        return True

    def start_scan(self, **kwargs):
        self.started.append(kwargs)
        return {"scan_id": "external-passive-scan"}

    def get_scan_status(self, scan_id):
        assert scan_id == "external-passive-scan"
        return self.status

    def get_scan_results(self, scan_id, limit=250):
        assert scan_id == "external-passive-scan"
        assert limit == 250
        return [
            {
                "type": "EMAILADDR",
                "data": "analyst@example.test",
                "sourceModule": "sfp_example",
                "sourceUrl": "https://example.test/evidence",
                "raw": ["not persisted"],
            }
        ]

    def get_result_summary(self, results):
        return {"EMAILADDR": len(results)}


def test_worker_starts_and_completes_only_passive_scan(auth_client, monkeypatch):
    _enable_workflow_source_research()
    case, investigation, subject = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="email",
        target_value="analyst@example.test",
        subject=subject,
    )
    db.session.commit()
    fake = _FakeSpiderFoot()
    monkeypatch.setattr(source_worker, "get_source_research_service", lambda: fake)

    assert source_worker.start_one_source_research() == "started"
    db.session.expire_all()
    persisted_scan = db.session.get(SpiderFootScan, scan.id)
    persisted_action = db.session.get(WorkflowResearchAction, action.id)
    assert persisted_scan.status == persisted_action.status == "running"
    assert persisted_scan.scan_id == "external-passive-scan"
    assert fake.started == [
        {
            "target": "analyst@example.test",
            "target_type": "EMAILADDR",
            "scan_name": "Passive source research: analyst@example.test",
            "use_case": "passive",
            "profile": "investigation",
        }
    ]

    assert source_worker.refresh_one_source_research() == "completed"
    db.session.expire_all()
    persisted_scan = db.session.get(SpiderFootScan, scan.id)
    persisted_action = db.session.get(WorkflowResearchAction, action.id)
    assert persisted_scan.status == persisted_action.status == "completed"
    assert persisted_scan.result_count == 1
    assert persisted_scan.result_summary == {
        "summary": {"EMAILADDR": 1},
        "proposals": [
            {
                "type": "EMAILADDR",
                "data": "analyst@example.test",
                "source_module": "sfp_example",
                "source_url": "https://example.test/evidence",
            }
        ],
    }
    assert "raw" not in str(persisted_scan.result_summary)


def test_worker_honours_feature_kill_switch_before_external_start(auth_client, monkeypatch):
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="domain",
        target_value="example.test",
    )
    db.session.commit()
    FeatureFlag.query.filter_by(
        tenant_id=case.tenant_id, flag_name="workflow_spiderfoot"
    ).update({"enabled": False})
    db.session.commit()
    monkeypatch.setattr(
        source_worker,
        "get_source_research_service",
        lambda: (_ for _ in ()).throw(AssertionError("external service must not run")),
    )

    assert source_worker.start_one_source_research() == "disabled"
    db.session.expire_all()
    assert db.session.get(SpiderFootScan, scan.id).status == "failed"
    assert db.session.get(WorkflowResearchAction, action.id).status == "error"


def test_status_route_exposes_only_completed_bounded_proposals(auth_client):
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="domain",
        target_value="example.test",
    )
    scan.status = "completed"
    scan.progress = 100
    scan.result_count = 1
    scan.result_summary = {
        "proposals": [{"type": "DOMAIN_NAME", "data": "example.test"}],
        "summary": {"DOMAIN_NAME": 1},
    }
    action.status = "completed"
    db.session.commit()

    response = auth_client.get(_status_url(case.id, investigation.id, action.id))
    assert response.status_code == 200
    assert response.get_json()["scan"] == {
        "id": scan.id,
        "status": "completed",
        "progress": 100,
        "result_count": 1,
        "proposals": [{"type": "DOMAIN_NAME", "data": "example.test"}],
    }
