"""PostgreSQL tenant session context shared by web, worker, and CLI paths."""

from sqlalchemy import text


def set_tenant_context(db, tenant_id: str | None, *, bypass_rls: bool = False) -> None:
    """Set the session-level RLS context for PostgreSQL Row-Level Security.

    Uses ``set_config(…, false)`` which sets the value at *session* scope
    (i.e. per-connection).  The implicit transaction opened by
    ``db.session.execute()`` is closed by the next ``commit()`` or
    ``rollback()`` in the normal request/worker lifecycle — an explicit
    ``rollback()`` here would undo the ``set_config`` values because
    PostgreSQL rolls back GUC changes made within the rolled-back
    transaction.
    """
    if db.engine.dialect.name != "postgresql":
        return

    db.session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
        {"tenant_id": tenant_id or ""},
    )
    db.session.execute(
        text("SELECT set_config('app.bypass_rls', :bypass_rls, false)"),
        {"bypass_rls": "true" if bypass_rls else "false"},
    )
