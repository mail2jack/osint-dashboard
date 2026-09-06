"""Regression: run_action must re-assert the RLS tenant context after commits.

A mid-run ``db.session.commit()`` returns the pooled connection; the next
query may rebind to a *different* PostgreSQL backend connection on which the
session-scoped RLS GUC (``app.tenant_id``) is empty.  The finding inserts would
then violate the ``WITH CHECK`` policy on ``findings`` (InsufficientPrivilege).
run_action must re-run `set_tenant_context` right after the "running"-status
commit and again right before persisting findings.
"""

import contextlib

from cms.workflow.actions import registry
from cms.workflow.models import WorkflowCase


class _FakeSession:
    def __init__(self, events, action):
        self._events = events
        self._action = action
        self.no_autoflush = contextlib.nullcontext()

    def get(self, model, action_id):
        if model is WorkflowCase:
            return None
        return self._action

    def expire(self, obj):
        pass

    def add(self, obj):
        self._events.append(("add",))

    def flush(self):
        self._events.append(("flush",))

    def commit(self):
        self._events.append(("commit",))

    def rollback(self):
        self._events.append(("rollback",))


def test_run_action_reasserts_tenant_context_after_running_commit(
    app, monkeypatch
):
    tenant_id = "tenant-11111111-2222-3333-4444-555555555555"
    action = type(
        "Action",
        (),
        {
            "tenant_id": tenant_id,
            "case_id": "case-1",
            "action_type": "email",
            "id": "action-1",
            "status": "pending",
            "created_by": None,
            "error": None,
            "data_value": "test@example.com",
        },
    )()

    events = []
    fake_session = _FakeSession(events, action)

    def fake_set_tenant_context(dbc, tenant, *, bypass_rls=False):
        events.append(("ctx", tenant, bypass_rls))

    events.append(("session_created",))

    monkeypatch.setattr(
        "cms.workflow.actions.registry.db", type("DB", (), {"session": fake_session})()
    )
    monkeypatch.setattr(
        "cms.tenant_context.set_tenant_context", fake_set_tenant_context
    )
    monkeypatch.setattr(
        "cms.workflow.actions.registry.ACTION_REGISTRY",
        {"email": {"handler": lambda a: [], "icon": "📧"}},
    )
    monkeypatch.setattr(
        "cms.workflow.actions.registry.is_paid_action", lambda t: False
    )
    monkeypatch.setattr(
        "cms.workflow.actions.registry.paid_channels_enabled", lambda *a: True
    )
    monkeypatch.setattr(
        "cms.workflow.actions.registry.is_action_cancelled", lambda i: False
    )
    monkeypatch.setattr(
        "cms.services.invoice_service.auto_invoice_action_completed",
        lambda action: None,
    )

    registry.run_action("action-1")

    ctx_calls = [e for e in events if e[0] == "ctx" and e[1] == tenant_id]
    commits = [e for e in events if e[0] == "commit"]

    # The tenant context is asserted at least 3 times: initial scope, after the
    # running-status commit, and right before the findings persist (including
    # with an empty findings list).
    assert len(ctx_calls) >= 3, f"events: {events}"

    # At least one re-assert must happen AFTER the first commit (this is the
    # "running"-status commit that was dropping the pooled connection).
    first_commit_idx = next(i for i, e in enumerate(events) if e[0] == "commit")
    post_commit_ctx = [
        e for e in events[first_commit_idx + 1 :] if e[0] == "ctx" and e[1] == tenant_id
    ]
    assert post_commit_ctx, f"no tenant re-assert after running commit: {events}"

    assert len(commits) >= 2, f"expected running + completion commits: {events}"
    # And the run completed cleanly (no rollback surfaced).
    assert not [e for e in events if e[0] == "rollback"]