"""PostgreSQL tenant session context shared by web, worker, and CLI paths."""

from sqlalchemy import text


def set_tenant_context(db, tenant_id: str | None, *, bypass_rls: bool = False) -> None:
    """Set the transaction-local RLS context when using PostgreSQL."""
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
