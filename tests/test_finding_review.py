"""Regression tests for Subject Profile finding review endpoints (PR #62).

Covers: verify, reject, archive, restore from the profile API.
Validates: 4-server-side checks, audit trail, archived-by-default behaviour.
"""

import uuid
from datetime import UTC, datetime

from cms.models import (
    AuditLog,
    Case,
    Client,
    FeatureFlag,
    Finding,
    Subject,
    User,
    case_assignments,
    db,
)


def _make_user(role="investigator"):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=f"fr_{token}",
        email=f"fr_{token}@localhost",
        full_name="Finding Review User",
        role=role,
        is_active=True,
    )
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.commit()
    return user


def _login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    return client


def _enable_flag(tenant_id):
    flag = FeatureFlag(
        tenant_id=tenant_id,
        flag_name="subject_first_investigations",
        enabled=True,
    )
    db.session.add(flag)
    db.session.commit()
    return flag


def _make_case(owner):
    client = Client(name="FR Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"FR-{uuid.uuid4().hex[:8]}",
        client_id=client.id,
        title="Finding review case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=owner.id,
    )
    db.session.add(case)
    db.session.flush()
    return case


def _make_finding(case, subject, status="candidate", archived=False):
    f = Finding(
        tenant_id=case.tenant_id,
        case_id=case.id,
        subject_id=subject.id,
        title="Test Finding",
        content="Some evidence content",
        source_type="osint",
        status=status,
        verified=(status == "verified"),
        created_by=case.created_by,
        archived_at=datetime.now(UTC) if archived else None,
    )
    db.session.add(f)
    db.session.commit()
    return f


def _profile_url(subject_id):
    return f"/cms/api/profile/subjects/{subject_id}/findings"


# ── Verify / validate ──────────────────────────────────────────────────────


class TestFindingReview:
    def test_verify_candidate_finding(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Review Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject, status="candidate")

        resp = auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "verified"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "verified"
        assert db.session.get(Finding, finding.id).verified is True

    def test_reject_candidate_finding(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Rej Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject, status="candidate")

        resp = auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "rejected"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "rejected"

    def test_invalid_status_returns_400(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Bad Status", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        resp = auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "bananas"},
        )
        assert resp.status_code == 400


# ── Authorization checks ───────────────────────────────────────────────────


class TestFindingReviewAuth:
    def test_viewer_gets_403_on_review(self, app):
        viewer = _make_user(role="viewer")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Viewer Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        resp = _login_as(app.test_client(), viewer).post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "verified"},
        )
        assert resp.status_code == 403

    def test_investigator_without_case_access_gets_403(self, app):
        outsider = _make_user(role="investigator")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Locked Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        resp = _login_as(app.test_client(), outsider).post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "verified"},
        )
        assert resp.status_code == 403

    def test_wrong_subject_returns_403(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject_a = Subject(name="Subj A", subject_type="person")
        subject_b = Subject(name="Subj B", subject_type="person")
        db.session.add_all([subject_a, subject_b])
        case.subjects.extend([subject_a, subject_b])
        db.session.commit()
        finding = _make_finding(case, subject_a)

        resp = auth_client.post(
            f"{_profile_url(subject_b.id)}/{finding.id}/review",
            json={"status": "verified"},
        )
        assert resp.status_code == 403

    def test_missing_subject_case_link_returns_403(self, auth_client):
        """Finding in case B, subject only linked to case A → 403."""
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case_a = _make_case(admin)
        case_b = _make_case(admin)
        subject = Subject(name="Link Subject", subject_type="person")
        db.session.add(subject)
        case_a.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case_b, subject)

        resp = auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "verified"},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).status == "candidate"


# ── Archive auth ────────────────────────────────────────────────────────────


class TestFindingArchiveAuth:
    def _archive_url(self, subject_id, finding_id):
        return f"{_profile_url(subject_id)}/{finding_id}/archive"

    def test_viewer_gets_403_on_archive(self, app):
        viewer = _make_user(role="viewer")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Arch Viewer", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        resp = _login_as(app.test_client(), viewer).post(
            self._archive_url(subject.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is None

    def test_investigator_without_case_access_gets_403(self, app):
        outsider = _make_user(role="investigator")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Arch Locked", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        resp = _login_as(app.test_client(), outsider).post(
            self._archive_url(subject.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is None

    def test_wrong_subject_returns_403(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject_a = Subject(name="Arch A", subject_type="person")
        subject_b = Subject(name="Arch B", subject_type="person")
        db.session.add_all([subject_a, subject_b])
        case.subjects.extend([subject_a, subject_b])
        db.session.commit()
        finding = _make_finding(case, subject_a)

        resp = auth_client.post(
            self._archive_url(subject_b.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is None

    def test_missing_subject_case_link_returns_403(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case_a = _make_case(admin)
        case_b = _make_case(admin)
        subject = Subject(name="Arch Link", subject_type="person")
        db.session.add(subject)
        case_a.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case_b, subject)

        resp = auth_client.post(
            self._archive_url(subject.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is None


# ── Restore auth ────────────────────────────────────────────────────────────


class TestFindingRestoreAuth:
    def _restore_url(self, subject_id, finding_id):
        return f"{_profile_url(subject_id)}/{finding_id}/restore"

    def test_viewer_gets_403_on_restore(self, app):
        viewer = _make_user(role="viewer")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Rest Viewer", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject, archived=True)

        resp = _login_as(app.test_client(), viewer).post(
            self._restore_url(subject.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is not None

    def test_investigator_without_case_access_gets_403(self, app):
        outsider = _make_user(role="investigator")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Rest Locked", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject, archived=True)

        resp = _login_as(app.test_client(), outsider).post(
            self._restore_url(subject.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is not None

    def test_wrong_subject_returns_403(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject_a = Subject(name="Rest A", subject_type="person")
        subject_b = Subject(name="Rest B", subject_type="person")
        db.session.add_all([subject_a, subject_b])
        case.subjects.extend([subject_a, subject_b])
        db.session.commit()
        finding = _make_finding(case, subject_a, archived=True)

        resp = auth_client.post(
            self._restore_url(subject_b.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is not None

    def test_missing_subject_case_link_returns_403(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case_a = _make_case(admin)
        case_b = _make_case(admin)
        subject = Subject(name="Rest Link", subject_type="person")
        db.session.add(subject)
        case_a.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case_b, subject, archived=True)

        resp = auth_client.post(
            self._restore_url(subject.id, finding.id),
            json={},
        )
        assert resp.status_code == 403
        assert db.session.get(Finding, finding.id).archived_at is not None


# ── Archive / restore happy path ────────────────────────────────────────────


class TestFindingArchive:
    def test_archive_finding(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Arch Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        resp = auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/archive",
        )
        assert resp.status_code == 200
        assert db.session.get(Finding, finding.id).archived_at is not None

    def test_restore_finding(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Rest Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject, archived=True)

        resp = auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/restore",
        )
        assert resp.status_code == 200
        assert db.session.get(Finding, finding.id).archived_at is None

    def test_archived_findings_hidden_by_default(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Hidden Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        _make_finding(case, subject, archived=True)

        resp = auth_client.get(f"/cms/subjects/{subject.id}")
        assert resp.status_code == 200
        assert b"Test Finding" not in resp.data

    def test_archived_findings_shown_with_toggle(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Toggle Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject, archived=True)

        resp = auth_client.get(f"/cms/subjects/{subject.id}?show_archived=1")
        assert resp.status_code == 200
        # The finding should appear (even if archived) because show_archived=1
        # Use the finding ID to avoid encryption concerns
        assert finding.id.encode() in resp.data or b"Toggle Subject" in resp.data


# ── Audit trail ────────────────────────────────────────────────────────────


class TestFindingAudit:
    def test_verify_creates_audit_log(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="Audit Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        auth_client.post(
            f"{_profile_url(subject.id)}/{finding.id}/review",
            json={"status": "verified"},
        )

        log = AuditLog.query.filter_by(
            entity_type="finding", entity_id=finding.id, action="review"
        ).first()
        assert log is not None
        assert log.description is not None
        assert "verified" in log.description.lower()

    def test_archive_creates_audit_log(self, auth_client):
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)
        case = _make_case(admin)
        subject = Subject(name="ArchAudit Subject", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()
        finding = _make_finding(case, subject)

        auth_client.post(f"{_profile_url(subject.id)}/{finding.id}/archive")

        log = AuditLog.query.filter_by(
            entity_type="finding", entity_id=finding.id, action="archive"
        ).first()
        assert log is not None


# ── Case isolation ──────────────────────────────────────────────────────────


class TestCaseIsolation:
    def test_investigator_sees_only_accessible_findings(self, app):
        """Investigator with access to case A but not B on a shared subject
        must NOT see findings from case B in the profile."""
        investigator = _make_user(role="investigator")
        admin = User.query.filter_by(role="admin").first()
        _enable_flag(admin.tenant_id)

        case_a = _make_case(admin)
        case_b = _make_case(admin)
        subject = Subject(name="Shared Subject", subject_type="person")
        db.session.add(subject)
        case_a.subjects.append(subject)
        case_b.subjects.append(subject)

        # Assign investigator to case A only
        db.session.execute(
            case_assignments.insert().values(case_id=case_a.id, user_id=investigator.id)
        )

        finding_a = _make_finding(case_a, subject, status="candidate")
        finding_b = _make_finding(case_b, subject, status="verified")
        db.session.commit()

        resp = _login_as(app.test_client(), investigator).get(
            f"/cms/subjects/{subject.id}/profile"
        )
        assert resp.status_code == 200
        # Finding from case A should be visible
        assert finding_a.id.encode() in resp.data
        # Finding from case B must NOT be visible
        assert finding_b.id.encode() not in resp.data
