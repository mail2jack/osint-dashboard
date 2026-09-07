"""add tenant RLS coverage for background_tasks

Adds the missing ``tenant_id`` column and FORCE RLS policy for
``background_tasks``, closing the last known gap in the tenant isolation
matrix (previously not FORCE-protected; persisted background jobs were
readable across tenants).

Design notes:

- ``tenant_id`` is nullable: tenant-agnostic system tasks (e.g. periodic
  cleanup or admin-initiated email) legitimately have no owning tenant.
  The policy is fail-closed — a row whose ``tenant_id`` does not match
  ``app.tenant_id`` (or is NULL) is invisible to non-bypass web queries;
  the web status endpoint therefore only ever exposes tasks of the request
  tenant (or all tasks for a non-switched super admin via bypass).
- Worker paths (RQ worker in ``cms/tasks.py`` and the in-process thread
  pool in ``cms/background.py``) run with ``app.bypass_rls = true`` so they
  can update any task regardless of tenant; they never read task content on
  behalf of a tenant.
- Backfill: this migration only adds the column and policy. Existing rows
  (pre-tenant tasks) keep ``NULL`` and thereby become invisible to
  non-bypass queries, which is the fail-closed baseline. New tasks created
  through ``run_in_background`` carry the request tenant when one is
  resolveable.

Revision ID: e2f3a4b5c6d7
Revises: a6b7c8d9e0f1
Create Date: 2026-09-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("background_tasks", schema=None) as batch_op:
        if not _has_column(bind, "background_tasks", "tenant_id"):
            batch_op.add_column(
                sa.Column("tenant_id", sa.String(36), nullable=True)
            )
            batch_op.create_index("ix_background_tasks_tenant_id", ["tenant_id"])
            batch_op.create_foreign_key(
                "fk_background_tasks_tenant", "tenants", ["tenant_id"], ["id"]
            )

    if bind.dialect.name != "postgresql":
        return

    bind.execute(sa.text("ALTER TABLE background_tasks ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE background_tasks FORCE ROW LEVEL SECURITY"))
    bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON background_tasks"))
    bind.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON background_tasks
            USING (
                current_setting('app.bypass_rls', true) = 'true'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'true'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text("DROP POLICY IF EXISTS tenant_isolation ON background_tasks")
        )
        bind.execute(
            sa.text("ALTER TABLE background_tasks NO FORCE ROW LEVEL SECURITY")
        )
        bind.execute(
            sa.text("ALTER TABLE background_tasks DISABLE ROW LEVEL SECURITY")
        )

    with op.batch_alter_table("background_tasks", schema=None) as batch_op:
        if _has_column(bind, "background_tasks", "tenant_id"):
            batch_op.drop_constraint("fk_background_tasks_tenant", type_="foreignkey")
            batch_op.drop_index("ix_background_tasks_tenant_id")
            batch_op.drop_column("tenant_id")


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}