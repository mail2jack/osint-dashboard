"""Investigation workspace read-model (PR3) — content, isolation, N+1, XSS, i18n.

Covers the read-only workspace service + detail route:
- route isolation (HTTP) vs. query isolation (service-level) as separate groups;
- content/regression rules from PR3_PLAN.md;
- bounded query count via a SQLAlchemy ``before_cursor_execute`` listener;
- XSS + URL-scheme hardening;
- NL/EN locale catalogs.

Run on SQLite (default ``tests/`` suite). PostgreSQL/RLS isolation lives in
``tests/test_postgres_investigation_workspace_rls.py``.
"""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from cms.models import (
    ActionFinding,
    AuditLog,
    Case,
    Client,
    FeatureFlag,
    Finding,
    Investigation,
    ResearchAction,
    Subject,
    Tenant,
    User,
    db,
)
from cms.services import investigation_workspace as ws_mod
from cms.services.investigation_workspace import (
    build_inv_workspace,
    load_inv_actions,
    load_inv_findings,
    load_inv_subjects,
    load_inv_timeline,
)

WORKSPACE_FLAG = "investigation_workspace"
TIMELINE_CAP = ws_mod.CAP


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _enable_workspace(tenant_id, enabled=True):
    flag = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name=WORKSPACE_FLAG
    ).first()
    if flag:
        flag.enabled = enabled
    else:
        flag = FeatureFlag(
            tenant_id=tenant_id, flag_name=WORKSPACE_FLAG, enabled=enabled
        )
        db.session.add(flag)
    db.session.commit()
    return flag


def _make_user(role, tenant_id=None, username=None):
    token = uuid.uuid4().hex[:8]
    user = User(
        username=username or f"ws_{token}",
        email=f"ws_{token}@localhost",
        full_name="Workspace Test User",
        role=role,
        is_active=True,
    )
    if tenant_id:
        user.tenant_id = tenant_id
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.flush()
    return user


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    return client


def _admin_tenant_id():
    return db.session.execute(
        db.text("SELECT tenant_id FROM users WHERE username='admin'")
    ).scalar()


def _make_other_tenant():
    tenant = Tenant(
        name=f"WS B {uuid.uuid4().hex[:8]}",
        slug=f"wsb-{uuid.uuid4().hex[:8]}",
        is_active=True,
        tier="enterprise",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(tenant)
    db.session.flush()
    return tenant


def _make_case(tenant_id=None, title="WS Case"):
    tenant_id = tenant_id or _admin_tenant_id()
    client = Client(name="WS Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"C-{uuid.uuid4().hex[:8].upper()}",
        client_id=client.id,
        title=title,
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.flush()
    return case


def _make_investigation(case, title="WS Onderzoek", **kwargs):
    kwargs.setdefault("sequence_no", 1)
    kwargs.setdefault("status", "open")
    inv = Investigation(
        tenant_id=case.tenant_id,
        case_id=case.id,
        title=title,
        **kwargs,
    )
    db.session.add(inv)
    db.session.flush()
    return inv


def _make_subject(case, name="Jan Jansen", email="jan@example.com", **kwargs):
    subject = Subject(
        tenant_id=case.tenant_id,
        name=name,
        subject_type=kwargs.pop("subject_type", "person"),
        email=email,
        **kwargs,
    )
    subject.encrypt_identifiers()
    db.session.add(subject)
    db.session.flush()
    return subject


def _make_action(case, investigation=None, subject=None, label="Dork", **kwargs):
    action = ResearchAction(
        case_id=case.id,
        tenant_id=case.tenant_id,
        subject_id=subject.id if subject else None,
        investigation_id=investigation.id if investigation else None,
        target_kind="subject" if subject else None,
        action_type=kwargs.pop("action_type", "google_dork"),
        label=label,
        **kwargs,
    )
    db.session.add(action)
    db.session.flush()
    return action


def _make_finding(case, subject, title="WS Finding", **kwargs):
    finding = Finding(
        tenant_id=case.tenant_id,
        case_id=case.id,
        subject_id=subject.id,
        title=title,
        content=kwargs.pop("content", "evidence content"),
        source_type=kwargs.pop("source_type", "web"),
        status=kwargs.pop("status", "candidate"),
        verified=kwargs.pop("verified", False),
        created_by=kwargs.get("created_by"),
        archived_at=kwargs.get("archived_at"),
        source_url=kwargs.get("source_url"),
        detail=kwargs.get("detail"),
        include_in_report=kwargs.get("include_in_report", True),
    )
    db.session.add(finding)
    db.session.flush()
    return finding


def _link(action, finding):
    db.session.add(ActionFinding(action_id=action.id, finding_id=finding.id))
    db.session.flush()


def _audit(
    user,
    action,
    entity_type,
    entity_id,
    case,
    *,
    old_values=None,
    new_values=None,
    description=None,
    timestamp=None,
):
    AuditLog.log(
        user_id=str(user.id),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case.id,
        tenant_id=case.tenant_id,
        old_values=old_values,
        new_values=new_values,
        description=description,
    )
    entry = AuditLog.query.filter_by(
        tenant_id=case.tenant_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case.id,
    ).order_by(AuditLog.timestamp.desc()).first()
    if timestamp is not None:
        entry.timestamp = timestamp
    db.session.commit()
    return entry


def _detail_url(case_id, investigation_id):
    return f"/cms/workflow/case/{case_id}/investigations/{investigation_id}"


def _scaffold(case, investigation, user, n_actions=2, n_findings=2):
    """Actions + findings + junctions + audit trail for a workspace."""
    subject = _make_subject(case)
    acts = []
    for i in range(n_actions):
        acts.append(
            _make_action(
                case,
                investigation=investigation,
                subject=subject,
                label=f"Dork {i}",
                status="completed" if i else "pending",
                completed_at=datetime.now(UTC) if i else None,
            )
        )
        _audit(
            user,
            "create",
            "research_action",
            acts[-1].id,
            case,
        )
    founds = []
    for i in range(n_findings):
        founds.append(
            _make_finding(
                case,
                subject,
                title=f"Bevinding {i}",
                created_by=user.id,
            )
        )
        for j in range(i + 1):
            _link(acts[j], founds[-1])
        _audit(
            user,
            "create",
            "finding",
            founds[-1].id,
            case,
        )
    db.session.commit()
    return subject, acts, founds


@pytest.fixture
def query_counter(app):
    counts = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counts["n"] += 1

    sa.event.listen(db.engine, "before_cursor_execute", _count)
    yield counts
    sa.event.remove(db.engine, "before_cursor_execute", _count)


# ---------------------------------------------------------------------------
# Route isolation (HTTP) — a different-tenant user NEVER gets a 200
# ---------------------------------------------------------------------------


class TestWorkspaceRouteIsolation:
    def test_route_flag_off_404_even_for_admin(self, app, auth_client):
        case = _make_case()
        inv = _make_investigation(case)
        resp = auth_client.get(_detail_url(case.id, inv.id))
        assert resp.status_code == 404

    def test_route_other_tenant_never_200(self, app, auth_client):
        tid = _admin_tenant_id()
        case = _make_case(tenant_id=tid, title="SA GEHEIM")
        inv = _make_investigation(case, title="Geheim onderzoek")
        _enable_workspace(tid, enabled=True)
        db.session.commit()

        tenant_b = _make_other_tenant()
        other = _make_user("investigator", tenant_id=tenant_b.id)
        db.session.commit()
        client = _login_as(app.test_client(), other)

        resp = client.get(_detail_url(case.id, inv.id))
        assert resp.status_code in (403, 404)
        assert resp.status_code != 200
        body = resp.get_data(as_text=True)
        assert "Geheim onderzoek" not in body

    def test_route_wrong_case_404(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        case_a = _make_case()
        case_b = _make_case()
        inv = _make_investigation(case_a)
        resp = auth_client.get(_detail_url(case_b.id, inv.id))
        assert resp.status_code == 404

    def test_route_other_tenant_user_without_access_403(self, app):
        tid = _admin_tenant_id()
        case = _make_case(tenant_id=tid)
        inv = _make_investigation(case)
        _enable_workspace(tid, enabled=True)
        tenant_b = _make_other_tenant()
        other = _make_user("investigator", tenant_id=tenant_b.id)
        db.session.commit()
        client = _login_as(app.test_client(), other)
        resp = client.get(_detail_url(case.id, inv.id))
        assert resp.status_code in (403, 404)

    def test_viewer_can_read(self, app):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        case = _make_case()
        case.created_by = _make_user("viewer", tenant_id=tid).id
        db.session.commit()
        inv = _make_investigation(case)
        viewer = db.session.get(User, case.created_by)
        _scaffold(case, inv, viewer)
        client = _login_as(app.test_client(), viewer)
        resp = client.get(_detail_url(case.id, inv.id))
        assert resp.status_code == 200

    def test_user_without_case_access_403(self, app):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        case = _make_case()
        inv = _make_investigation(case)
        investigator = _make_user("investigator", tenant_id=tid)
        db.session.commit()
        client = _login_as(app.test_client(), investigator)
        resp = client.get(_detail_url(case.id, inv.id))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Query/service isolation (explicit tenant_id, no request context)
# ---------------------------------------------------------------------------


class TestWorkspaceQueryIsolation:
    def _data(self):
        tid = _admin_tenant_id()
        case = _make_case(tenant_id=tid)
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject, acts, founds = _scaffold(case, inv, user)
        db.session.commit()
        other_tenant = _make_other_tenant().id
        db.session.commit()
        return case.id, inv.id, subject.id, acts, founds, other_tenant

    def test_query_tenant_isolation_actions(self):
        case_id, inv_id, _, acts, _, tenant_b = self._data()
        assert load_inv_actions(
            tenant_id=tenant_b, case_id=case_id, investigation_id=inv_id
        ) == []
        assert load_inv_actions(
            tenant_id=_admin_tenant_id(), case_id=case_id, investigation_id=inv_id
        )

    def test_query_tenant_isolation_subjects(self):
        case_id, inv_id, subject_id, acts, _, tenant_b = self._data()
        assert (
            load_inv_subjects(tenant_id=tenant_b, subject_ids=[subject_id]) == []
        )
        assert load_inv_subjects(
            tenant_id=_admin_tenant_id(), subject_ids=[subject_id]
        )

    def test_query_tenant_isolation_findings(self):
        case_id, inv_id, _, acts, _, tenant_b = self._data()
        assert load_inv_findings(
            tenant_id=tenant_b,
            case_id=case_id,
            action_ids=[a.id for a in acts],
        ) == ([], {})
        assert load_inv_findings(
            tenant_id=_admin_tenant_id(),
            case_id=case_id,
            action_ids=[a.id for a in acts],
        )[0]

    def test_query_tenant_isolation_timeline(self):
        case_id, inv_id, _, acts, founds, tenant_b = self._data()
        result = load_inv_timeline(
            tenant_id=tenant_b,
            case_id=case_id,
            investigation_id=inv_id,
            action_ids=[a.id for a in acts],
            finding_ids=[f.id for f in founds],
        )
        assert result.total == 0
        assert result.events == []


# ---------------------------------------------------------------------------
# Content / regression
# ---------------------------------------------------------------------------


class TestWorkspaceContent:
    def test_soft_deleted_subject_excluded(self):
        case = _make_case()
        inv = _make_investigation(case)
        subject = _make_subject(case, name="Sophie Softdelete")
        subject.is_deleted = True
        db.session.commit()
        _make_action(case, investigation=inv, subject=subject, label="Dork S")
        db.session.commit()

        ws = build_inv_workspace(inv, None)
        assert ws.counts["subjects"] == 0
        assert all(s.name != "Sophie Softdelete" for s in ws.subjects)

    def test_deleted_finding_excluded(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        act = _make_action(case, investigation=inv, subject=subject)
        f = _make_finding(case, subject, title="Bevinding Del", created_by=user.id)
        f.is_deleted = True
        db.session.commit()
        _link(act, f)
        db.session.commit()

        ws = build_inv_workspace(inv, None)
        assert all(x.title != "Bevinding Del" for x in ws.findings)

    def test_archived_finding_excluded(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        act = _make_action(case, investigation=inv, subject=subject)
        f = _make_finding(
            case,
            subject,
            title="Bevinding Arch",
            created_by=user.id,
            archived_at=datetime.now(UTC),
        )
        db.session.commit()
        _link(act, f)
        db.session.commit()

        ws = build_inv_workspace(inv, None)
        assert all(x.title != "Bevinding Arch" for x in ws.findings)

    def test_archived_action_shown(self):
        case = _make_case()
        inv = _make_investigation(case)
        subject = _make_subject(case)
        _make_action(
            case,
            investigation=inv,
            subject=subject,
            label="Dork Gearchiveerd",
            archived_at=datetime.now(UTC),
        )
        db.session.commit()

        ws = build_inv_workspace(inv, None)
        by_label = {a.label: a for a in ws.actions}
        assert "Dork Gearchiveerd" in by_label
        assert by_label["Dork Gearchiveerd"].archived is True

    def test_finding_linked_to_multiple_scoped_actions(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        a1 = _make_action(case, investigation=inv, subject=subject, label="A 1")
        a2 = _make_action(case, investigation=inv, subject=subject, label="A 2")
        f = _make_finding(case, subject, created_by=user.id)
        _link(a1, f)
        _link(a2, f)
        db.session.commit()

        ws = build_inv_workspace(inv, None)
        assert len(ws.findings) == 1
        assert sorted(ws.findings[0].action_ids) == sorted([a1.id, a2.id])
        counts = {a.label: a.finding_count for a in ws.actions}
        assert counts == {"A 1": 1, "A 2": 1}

    def test_finding_also_linked_to_case_wide_action(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        a = _make_action(case, investigation=inv, subject=subject, label="Scoped A")
        y = _make_action(case, investigation=None, subject=None, label="Case-wide Y")
        f = _make_finding(case, subject, created_by=user.id)
        _link(a, f)
        _link(y, f)
        db.session.commit()

        ws = build_inv_workspace(inv, None)
        assert len(ws.findings) == 1
        assert ws.findings[0].action_ids == [a.id]
        assert y.id not in ws.findings[0].action_ids

    def test_action_ids_only_scoped(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        a = _make_action(case, investigation=inv, subject=subject, label="Scoped A")
        y = _make_action(case, investigation=None, subject=None, label="Case-wide Y")
        f = _make_finding(case, subject, created_by=user.id)
        _link(a, f)
        _link(y, f)
        db.session.commit()

        dto = build_inv_workspace(inv, None).findings[0]
        labels = dto.action_labels
        assert "Scoped A" in labels
        assert "Case-wide Y" not in labels

    def test_actions_sorted_created_at_asc(self):
        case = _make_case()
        inv = _make_investigation(case)
        subject = _make_subject(case)
        old = _make_action(
            case, investigation=inv, subject=subject, label="Old",
            created_at=datetime.now(UTC),
        )
        old.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        _make_action(
            case, investigation=inv, subject=subject, label="New",
            created_at=datetime.now(UTC),
        )
        db.session.commit()

        labels = [a.label for a in build_inv_workspace(inv, None).actions]
        assert labels == ["Old", "New"]

    def test_cross_product_prevention(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        _audit(
            user,
            "create",
            "finding",
            "some-unlinked-finding",
            case,
        )
        _audit(
            user,
            "create",
            "research_action",
            "case-wide-action-Y",
            case,
        )
        result = load_inv_timeline(
            tenant_id=case.tenant_id,
            case_id=case.id,
            investigation_id=inv.id,
            action_ids=[],
            finding_ids=[],
        )
        assert result.total == 0
        assert result.events == []

    def test_timeline_link_event_not_double_counted(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        a = _make_action(case, investigation=inv, subject=subject, label="Scoped A")
        _audit(
            user,
            "link",
            "research_action",
            a.id,
            case,
            old_values={"investigation_id": ""},
            new_values={"investigation_id": inv.id},
        )

        result = load_inv_timeline(
            tenant_id=case.tenant_id,
            case_id=case.id,
            investigation_id=inv.id,
            action_ids=[a.id],
            finding_ids=[],
        )
        assert len(result.events) == 1
        assert result.total == 1
        assert result.events[0].action == "link"

    def test_unlink_timeline_event_persists(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        db.session.flush()
        x = _make_action(case, investigation=None, label="Ooit Gekoppeld")
        _audit(
            user,
            "unlink",
            "research_action",
            x.id,
            case,
            old_values={"investigation_id": inv.id},
            new_values={"investigation_id": ""},
        )

        result = load_inv_timeline(
            tenant_id=case.tenant_id,
            case_id=case.id,
            investigation_id=inv.id,
            action_ids=[],
            finding_ids=[],
        )
        assert len(result.events) == 1
        assert result.total == 1
        assert result.events[0].action == "unlink"

    def test_timeline_capped_at_50(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        f = _make_finding(case, _make_subject(case), created_by=user.id)
        db.session.commit()
        for i in range(TIMELINE_CAP + 5):
            _audit(
                user,
                "comment",
                "finding",
                f.id,
                case,
                description=f"comment {i}",
            )

        result = load_inv_timeline(
            tenant_id=case.tenant_id,
            case_id=case.id,
            investigation_id=inv.id,
            action_ids=[],
            finding_ids=[f.id],
        )
        assert len(result.events) == TIMELINE_CAP
        assert result.total == TIMELINE_CAP + 5
        assert result.truncated is True

    def test_timeline_total_exact_when_b_window_not_full(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        a = _make_action(case, investigation=inv, subject=subject, label="Scoped A")
        _audit(
            user,
            "link",
            "research_action",
            a.id,
            case,
            old_values={"investigation_id": ""},
            new_values={"investigation_id": inv.id},
        )
        x = _make_action(case, investigation=None, label="Ooit Gekoppeld")
        _audit(
            user,
            "unlink",
            "research_action",
            x.id,
            case,
            old_values={"investigation_id": inv.id},
            new_values={"investigation_id": ""},
        )

        result = load_inv_timeline(
            tenant_id=case.tenant_id,
            case_id=case.id,
            investigation_id=inv.id,
            action_ids=[a.id],
            finding_ids=[],
        )
        assert result.total_exact is True
        assert result.total == 2
        assert len(result.events) == 2

    def test_timeline_total_undercount_when_b_window_full(self):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject = _make_subject(case)
        a = _make_action(case, investigation=inv, subject=subject, label="Scoped A")
        db.session.commit()
        # In-scope unlink from the deep past -> outside the CAP discovery window.
        x = _make_action(case, investigation=None, label="Ooit Gekoppeld")
        _audit(
            user,
            "unlink",
            "research_action",
            x.id,
            case,
            old_values={"investigation_id": inv.id},
            new_values={"investigation_id": ""},
            timestamp=datetime(2020, 1, 1, tzinfo=UTC),
        )
        # > CAP unrelated link/unlink audits flood the bounded window.
        for i in range(TIMELINE_CAP + 1):
            other = _make_action(
                case,
                investigation=None,
                label=f"Ander {i}",
                created_at=datetime.now(UTC),
            )
            _audit(
                user,
                "link",
                "research_action",
                other.id,
                case,
                old_values={"investigation_id": ""},
                new_values={"investigation_id": "other-inv"},
            )

        result = load_inv_timeline(
            tenant_id=case.tenant_id,
            case_id=case.id,
            investigation_id=inv.id,
            action_ids=[a.id],
            finding_ids=[],
        )
        assert result.total_exact is False
        assert result.truncated is True

    def test_no_ciphertext_in_html(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        case.created_by = user.id
        db.session.commit()
        _scaffold(case, inv, user)
        resp = auth_client.get(_detail_url(case.id, inv.id))
        assert resp.status_code == 200
        assert "gAAAA" not in resp.get_data(as_text=True)

    def test_ciphertext_unchanged_in_db(self, app, auth_client):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        case.created_by = user.id
        db.session.commit()
        subject, _, _ = _scaffold(case, inv, user)
        resp = auth_client.get(_detail_url(case.id, inv.id))
        assert resp.status_code == 200
        db.session.expire_all()
        stored = db.session.get(Subject, subject.id)
        assert stored.email.startswith("gAAAA")


# ---------------------------------------------------------------------------
# N+1 / query count (before_cursor_execute)
# ---------------------------------------------------------------------------


class TestWorkspaceQueryCount:
    def _build(self, n_events=5):
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        subject, acts, founds = _scaffold(
            case, inv, user, n_actions=5, n_findings=5
        )
        for i in range(n_events):
            _audit(user, "comment", "finding", founds[0].id, case)
        db.session.commit()
        return case, inv

    def test_query_count_bounded(self, query_counter):
        case, inv = self._build(n_events=8)
        baseline = query_counter["n"]
        build_inv_workspace(inv, case)
        # K = 8 constant queries (actions, junction, findings+joinedload,
        # audit A events, audit A count, audit B events, audit B count,
        # subjects) + 4 documented fixed overhead margin = 12.
        assert query_counter["n"] - baseline <= 12, query_counter["n"] - baseline

    def test_no_per_event_user_query(self, query_counter):
        case, inv = self._build(n_events=20)
        baseline = query_counter["n"]
        build_inv_workspace(inv, case)
        # 20 audit events must NOT trigger a user query per event: users are
        # eager-loaded via joinedload(AuditLog.user) in the audit queries.
        assert query_counter["n"] - baseline <= 12, query_counter["n"] - baseline


# ---------------------------------------------------------------------------
# XSS + URL-scheme hardening
# ---------------------------------------------------------------------------


class TestWorkspaceXssAndUrlScheme:
    def _get_with(self, auth_client, case, inv):
        return auth_client.get(_detail_url(case.id, inv.id))

    def _xss_page(self, *, title="WS", detail=None, source_url=None,
                  action_label="Dork", subject_name="Jan Jansen"):
        tid = _admin_tenant_id()
        _enable_workspace(tid, enabled=True)
        case = _make_case()
        inv = _make_investigation(case)
        user = User.query.filter_by(username="admin").first()
        case.created_by = user.id
        subject = _make_subject(case, name=subject_name)
        act = _make_action(case, investigation=inv, subject=subject, label=action_label)
        f = _make_finding(
            case,
            subject,
            title=title,
            detail=detail,
            source_url=source_url,
            created_by=user.id,
        )
        _link(act, f)
        db.session.commit()
        return case, inv, act, f, subject

    def test_xss_finding_detail(self, auth_client):
        case, inv, _, _, _ = self._xss_page(
            detail="<script>alert(1)</script> safe https://example.com/x"
        )
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body
        assert "https://example.com/x" in body

    def test_xss_finding_title(self, auth_client):
        case, inv, *_ = self._xss_page(title="<script>alert(2)</script>")
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert "alert(2)" in body
        assert "<script>alert(2)</script>" not in body

    def test_xss_finding_source_url(self, auth_client):
        case, inv, *_ = self._xss_page(
            source_url='<img src=x onerror="alert(3)">'
        )
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        # No live <img> or event handler in unescaped HTML — the payload appears
        # entity-escaped only (onerror=&#34;...) inside the text <span>.
        assert "<img " not in body
        assert "onerror=&#34;" in body  # escaped, safe

    def test_xss_action_label(self, auth_client):
        case, inv, *_ = self._xss_page(action_label="<script>alert(4)</script>")
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert "alert(4)" in body
        assert "<script>alert(4)</script>" not in body

    def test_xss_subject_name(self, auth_client):
        case, inv, *_ = self._xss_page(subject_name='<svg onload="alert(5)">')
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert "alert(5)" in body
        assert "<svg" not in body

    def test_source_url_javascript_scheme(self, auth_client):
        assert ws_mod._is_linkable_url("javascript:alert(1)") is False
        assert ws_mod._is_linkable_url("JAVAScript:alert(1)") is False
        case, inv, _, _, _ = self._xss_page(
            title="JS", source_url="javascript:alert(1)"
        )
        dto = build_inv_workspace(inv, case).findings[0]
        assert dto.source_url_is_linkable is False
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert "javascript:alert(1)" in body
        assert 'href="javascript:' not in body

    def test_source_url_data_scheme(self, auth_client):
        assert ws_mod._is_linkable_url("data:text/html,<b>x</b>") is False
        case, inv, _, _, _ = self._xss_page(
            title="Data", source_url="data:text/html,<b>x</b>"
        )
        dto = build_inv_workspace(inv, case).findings[0]
        assert dto.source_url_is_linkable is False
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert "data:text/html" in body
        assert 'href="data:' not in body

    def test_source_url_https_linkable(self, auth_client):
        assert ws_mod._is_linkable_url("https://example.com/x") is True
        case, inv, _, _, _ = self._xss_page(
            title="HTTPS", source_url="https://example.com/x?a=1&b=2"
        )
        dto = build_inv_workspace(inv, case).findings[0]
        assert dto.source_url_is_linkable is True
        body = self._get_with(auth_client, case, inv).get_data(as_text=True)
        assert 'href="https://example.com/x?a=1&amp;b=2"' in body


# ---------------------------------------------------------------------------
# Locale catalogs (NL msgstr, EN msgid) — one shared msgid set
# ---------------------------------------------------------------------------


class TestWorkspaceLocaleCatalog:
    def _po(self, locale):
        import os

        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "translations",
            locale,
            "LC_MESSAGES",
            "messages.po",
        )
        with open(path) as f:
            return f.read()

    def test_nl_locale_strings(self):
        po = self._po("nl")
        assert (
            'msgid "Subjects referenced by research actions"\n'
            'msgstr "Subjects betrokken via onderzoeksacties"' in po
        )
        assert 'msgid "Activity"\nmsgstr "Activiteiten"' in po

    def test_en_locale_strings(self):
        po = self._po("en")
        assert 'msgid "Subjects referenced by research actions"' in po
        assert 'msgid "Activity"' in po