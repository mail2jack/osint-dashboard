"""
ADR-0001 PR8 — rollout controls: per-tenant pilot flag, global kill-switch,
legacy screen gating, and the audited research_actions.subject_id backfill.
"""

import importlib.util
import json
import os
import uuid

from cms.models import (
    AuditLog,
    Case,
    FeatureFlag,
    ResearchAction,
    Subject,
    Tenant,
    User,
    db,
    set_setting,
)
from cms.tier_limits import check_feature

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
_SCRIPT_PATH = os.path.join(_SCRIPTS_DIR, "backfill_subject_actions.py")

_spec = importlib.util.spec_from_file_location("backfill_subject_actions", _SCRIPT_PATH)
_backfill_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backfill_module)
backfill = _backfill_module.backfill


def _enable_flag(tenant_id):
    flag = FeatureFlag(
        tenant_id=tenant_id,
        flag_name="subject_first_investigations",
        enabled=True,
    )
    db.session.add(flag)
    db.session.commit()
    return flag


def _admin():
    return User.query.filter_by(username="admin").first()


def _case_with_subject(auth_client, title="Rollout Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "Rollout Client",
            "title": title,
            "subject_0_name": "Rollout Person",
            "subject_0_type": "person",
            "subject_0_email": "rollout@example.com",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    case = Case.query.filter_by(title=title).first()
    assert case is not None
    return case


class TestKillSwitch:
    def test_kill_switch_forces_flag_off_despite_override(self):
        admin = _admin()
        tenant_id = admin.tenant_id
        _enable_flag(tenant_id)
        assert check_feature("subject_first_investigations", tenant_id) is True

        set_setting(
            "subject_first_investigations_global", "0", category="feature_flags"
        )
        assert check_feature("subject_first_investigations", tenant_id) is False

        set_setting(
            "subject_first_investigations_global", "1", category="feature_flags"
        )
        assert check_feature("subject_first_investigations", tenant_id) is True

    def test_kill_switch_does_not_touch_other_features(self):
        admin = _admin()
        tenant_id = admin.tenant_id
        set_setting(
            "subject_first_investigations_global", "0", category="feature_flags"
        )
        assert check_feature("subject_first_investigations", tenant_id) is False
        assert check_feature("paid_channels", tenant_id) is False

    def test_flag_defaults_off_without_override(self):
        admin = _admin()
        assert check_feature("subject_first_investigations", admin.tenant_id) is False


class TestLegacyGating:
    def test_edit_redirects_to_profile_when_flag_on(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Gate Edit")
        subject = case.subjects[0]

        resp = auth_client.get(f"/cms/subjects/{subject.id}/edit")
        assert resp.status_code == 302, resp.status_code
        assert resp.headers["Location"].endswith(f"/subjects/{subject.id}/profile"), (
            resp.headers["Location"]
        )

    def test_edit_json_403_when_flag_on(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Gate Edit JSON")
        subject = case.subjects[0]

        resp = auth_client.post(f"/cms/subjects/{subject.id}/edit", json={"name": "X"})
        assert resp.status_code == 403, resp.status_code
        assert resp.is_json

    def test_edit_renders_when_flag_off(self, auth_client):
        case = _case_with_subject(auth_client, title="Gate Edit Off")
        subject = case.subjects[0]

        resp = auth_client.get(f"/cms/subjects/{subject.id}/edit")
        assert resp.status_code == 200, resp.status_code

    def test_create_redirects_when_flag_on_without_case(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)

        resp = auth_client.get("/cms/subjects/create")
        assert resp.status_code == 302, resp.status_code
        assert resp.headers["Location"].endswith("/cms/subjects")

    def test_create_json_403_when_flag_on_without_case(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)

        resp = auth_client.post(
            "/cms/subjects/create",
            json={"name": "Standalone", "subject_type": "person"},
        )
        assert resp.status_code == 403, resp.status_code
        assert resp.is_json
        assert Subject.query.filter_by(name="Standalone").first() is None

    def test_create_from_case_still_works_when_flag_on(self, auth_client):
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Gate Create Case")
        before = len(list(case.subjects))

        resp = auth_client.post(
            "/cms/subjects/create",
            json={
                "name": "Case Flow Person",
                "subject_type": "person",
                "case_id": str(case.id),
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

        subject = Subject.query.filter_by(name="Case Flow Person").first()
        assert subject is not None
        assert subject.id in {s.id for s in case.subjects}
        assert len(list(case.subjects)) == before + 1


class TestBackfill:
    def _fixture(self, auth_client, title="Backfill Case"):
        admin = _admin()
        case = _case_with_subject(auth_client, title=title)
        subject = case.subjects[0]
        return admin, case, subject

    def _action(self, case, subject, snapshot_subject_id, target_kind="subject"):
        action = ResearchAction(
            tenant_id=subject.tenant_id,
            case_id=case.id,
            subject_id=None,
            target_kind=target_kind,
            target_snapshot=json.dumps({"subject_id": snapshot_subject_id}),
            action_type="osint_search",
            status="completed",
        )
        db.session.add(action)
        db.session.commit()
        return action

    def test_dry_run_reports_and_writes_nothing(self, auth_client):
        admin, case, subject = self._fixture(auth_client, title="Backfill Dry")
        action = self._action(case, subject, str(subject.id))

        db.session.refresh(action)
        backfill(apply=False)
        db.session.refresh(action)
        assert action.subject_id is None

    def test_apply_links_and_audits_then_is_idempotent(self, auth_client):
        admin, case, subject = self._fixture(auth_client, title="Backfill Apply")
        action = self._action(case, subject, str(subject.id))
        tenant_id = subject.tenant_id

        backfill(apply=True)

        db.session.refresh(action)
        assert action.subject_id == str(subject.id)

        audit = AuditLog.query.filter_by(action="backfill").all()
        assert len(audit) == 1
        assert audit[0].tenant_id == tenant_id
        assert "1" in audit[0].description

        audit_count = len(audit)
        backfill(apply=True)
        db.session.refresh(action)
        assert action.subject_id == str(subject.id)
        assert len(AuditLog.query.filter_by(action="backfill").all()) == audit_count

    def test_skips_ambiguous_or_invalid_rows(self, auth_client):
        admin, case, subject = self._fixture(auth_client, title="Backfill Skips")

        missing_snapshot = self._action(case, subject, None)
        missing_snapshot.target_snapshot = json.dumps({"data_value": "query"})
        db.session.commit()

        gone = Subject(
            name="Deleted Person",
            subject_type="person",
            tenant_id=subject.tenant_id,
            created_by=admin.id,
            is_deleted=True,
        )
        db.session.add(gone)
        db.session.commit()
        deleted_target = self._action(case, subject, str(gone.id))

        other_tenant = Tenant(
            name="Other Tenant",
            slug=f"other-{uuid.uuid4().hex[:8]}",
            join_code=uuid.uuid4().hex[:8],
            tier="free",
        )
        db.session.add(other_tenant)
        db.session.commit()
        other_subject = Subject(
            name="Other Tenant Person",
            subject_type="person",
            tenant_id=other_tenant.id,
            created_by=admin.id,
        )
        db.session.add(other_subject)
        db.session.commit()
        cross_tenant = self._action(case, subject, str(other_subject.id))

        case_wide = self._action(case, subject, str(subject.id), target_kind="case")

        backfill(apply=True)

        db.session.refresh(missing_snapshot)
        db.session.refresh(deleted_target)
        db.session.refresh(cross_tenant)
        db.session.refresh(case_wide)
        assert missing_snapshot.subject_id is None
        assert deleted_target.subject_id is None
        assert cross_tenant.subject_id is None
        assert case_wide.subject_id is None
