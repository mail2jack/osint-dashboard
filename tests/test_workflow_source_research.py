"""Queue-contract tests for workflow-native passive source research."""

import uuid

import pytest

from cms.models import AuditLog, Case, FeatureFlag, Investigation, SpiderFootScan, User, db
from cms.services.workflow_source_research import (
    SourceResearchRejected,
    TARGET_TYPES,
    passive_profile_for,
    queue_passive_source_research,
    spiderfoot_seed_for,
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


def _enable_flag(flag_name):
    tenant_id = _admin().tenant_id
    flag = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name=flag_name
    ).first()
    if flag is None:
        flag = FeatureFlag(
            tenant_id=tenant_id, flag_name=flag_name, enabled=True
        )
        db.session.add(flag)
    else:
        flag.enabled = True
    db.session.commit()


def _enable_workflow_source_research():
    _enable_flag("workflow_spiderfoot")


def _request_url(case_id, investigation_id):
    return (
        f"/cms/workflow/api/case/{case_id}/investigations/"
        f"{investigation_id}/source-research"
    )


def _status_url(case_id, investigation_id, action_id):
    return f"{_request_url(case_id, investigation_id)}/{action_id}"


def _import_url(case_id, investigation_id, action_id):
    return f"{_status_url(case_id, investigation_id, action_id)}/import"


def _case_wide_url(case_id):
    return f"/cms/workflow/api/case/{case_id}/source-research"


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
    assert spiderfoot_seed_for("person", "Lindsey Jonker") == (
        '"Lindsey Jonker"',
        "HUMAN_NAME",
    )
    assert spiderfoot_seed_for("username", "research_user") == (
        '"research_user"',
        "USERNAME",
    )


def test_target_contract_rejects_quotes_in_spiderfoot_syntax():
    with pytest.raises(SourceResearchRejected, match="quotation"):
        spiderfoot_seed_for("person", 'Lindsey "Jonker"')


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


def test_queue_quotes_person_seed_for_spiderfoot(auth_client):
    case, investigation, subject = _case_with_open_investigation(auth_client)
    _, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="person",
        target_value="Lindsey Jonker",
        subject=subject,
    )
    assert scan.target_value == '"Lindsey Jonker"'
    assert scan.target_type == "HUMAN_NAME"


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


def test_workspace_renders_source_research_control_only_when_enabled(auth_client):
    _enable_flag("investigation_workspace")
    case, investigation, _ = _case_with_open_investigation(auth_client)
    detail_url = f"/cms/workflow/case/{case.id}/investigations/{investigation.id}"
    assert 'value="source_research"' not in auth_client.get(detail_url).get_data(as_text=True)

    _enable_workflow_source_research()
    html = auth_client.get(detail_url).get_data(as_text=True)
    assert 'value="source_research"' in html
    assert 'data-open-source-research style=' not in html
    assert 'id="wsSourceResearchModal"' in html
    assert 'name="source_target_type"' in html
    assert 'value="ipv6"' in html
    assert "use_case: 'all'" not in html


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


def test_route_queues_case_wide_passive_research(auth_client):
    _enable_workflow_source_research()
    case, _, subject = _case_with_open_investigation(auth_client)

    response = auth_client.post(
        _case_wide_url(case.id),
        json={
            "target_type": "domain",
            "target_value": "example.test",
            "subject_id": subject.id,
            "investigation_id": None,
        },
    )
    assert response.status_code == 202
    body = response.get_json()
    action = db.session.get(WorkflowResearchAction, body["action"]["id"])
    scan = db.session.get(SpiderFootScan, body["scan"]["id"])
    assert action.investigation_id is None
    assert scan.investigation_id is None
    assert action.target_kind == "subject"


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


def test_worker_refresh_skips_unstarted_placeholder_when_real_scan_runs(
    auth_client, monkeypatch
):
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    stalled_action, stalled_scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="domain",
        target_value="stalled.example.test",
    )
    active_action, active_scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="domain",
        target_value="active.example.test",
    )
    stalled_action.status = active_action.status = "running"
    stalled_scan.status = active_scan.status = "running"
    active_scan.scan_id = "external-active-scan"
    db.session.commit()

    fake = _FakeSpiderFoot(status={"status": "running", "progress": 42})
    fake.get_scan_status = lambda scan_id: {"status": "running", "progress": 42}
    monkeypatch.setattr(source_worker, "get_source_research_service", lambda: fake)

    assert source_worker.refresh_one_source_research() == "running"
    db.session.expire_all()
    assert db.session.get(SpiderFootScan, active_scan.id).progress == 42
    stalled = db.session.get(SpiderFootScan, stalled_scan.id)
    assert stalled.scan_id.startswith("queued:")
    assert stalled.progress != 42


def test_completed_source_research_action_has_reopenable_review_control(auth_client):
    _enable_flag("investigation_workspace")
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="domain",
        target_value="example.test",
    )
    action.status = scan.status = "completed"
    db.session.commit()

    html = auth_client.get(
        f"/cms/workflow/case/{case.id}/investigations/{investigation.id}"
    ).get_data(as_text=True)
    assert f'data-source-action-id="{action.id}"' in html
    assert "data-review-source-research" in html


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


def test_case_wide_status_route_is_scoped_to_case_wide_action(auth_client):
    _enable_workflow_source_research()
    case, _, _ = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        actor=_admin(),
        target_type="domain",
        target_value="example.test",
    )
    action.status = scan.status = "completed"
    scan.result_summary = {"proposals": []}
    db.session.commit()

    response = auth_client.get(f"{_case_wide_url(case.id)}/{action.id}")
    assert response.status_code == 200
    assert response.get_json()["action"]["id"] == action.id


def test_import_route_creates_selected_linked_candidate_once(auth_client):
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
    action.status = "completed"
    scan.status = "completed"
    scan.result_summary = {
        "proposals": [
            {
                "type": "EMAILADDR",
                "data": "analyst@example.test",
                "source_module": "sfp_example",
                "source_url": "https://example.test/evidence",
            }
        ]
    }
    db.session.commit()

    response = auth_client.post(
        _import_url(case.id, investigation.id, action.id),
        json={"proposal_indexes": [0]},
    )
    assert response.status_code == 200
    finding_id = response.get_json()["finding_ids"][0]
    from cms.models import Finding
    from cms.workflow.models import WorkflowActionFinding

    finding = db.session.get(Finding, finding_id)
    assert finding is not None
    assert finding.case_id == case.id
    assert finding.subject_id == subject.id
    assert finding.status == "candidate"
    assert finding.source_type == "spiderfoot"
    assert finding.raw_data["proposal_index"] == 0
    assert WorkflowActionFinding.query.filter_by(
        action_id=action.id, finding_id=finding.id
    ).count() == 1
    assert db.session.get(SpiderFootScan, scan.id).result_summary["imported_indexes"] == [0]

    repeat = auth_client.post(
        _import_url(case.id, investigation.id, action.id),
        json={"proposal_indexes": [0]},
    )
    assert repeat.status_code == 200
    assert repeat.get_json() == {"ok": True, "imported": 0, "finding_ids": []}


def test_import_route_rejects_invalid_selection_without_findings(auth_client):
    _enable_workflow_source_research()
    case, investigation, _ = _case_with_open_investigation(auth_client)
    action, scan = queue_passive_source_research(
        case=case,
        investigation=investigation,
        actor=_admin(),
        target_type="domain",
        target_value="example.test",
    )
    action.status = scan.status = "completed"
    scan.result_summary = {"proposals": [{"type": "DOMAIN_NAME", "data": "example.test"}]}
    db.session.commit()
    from cms.models import Finding

    before = Finding.query.count()
    response = auth_client.post(
        _import_url(case.id, investigation.id, action.id),
        json={"proposal_indexes": [1]},
    )
    assert response.status_code == 400
    assert Finding.query.count() == before
