"""FORCE RLS for research_actions + action_findings (ADR-0005 closure)

``research_actions`` is the only ADR-0005 table storing job data that never
got FORCE RLS: it carries its own NOT NULL ``tenant_id`` but was left out of
the tenant isolation matrix, so request / worker / CLI paths that read or
write research actions were not guarded by a row-level policy.

Closes the gap:

- ``research_actions``: ENABLE + FORCE RLS with the standard
  ``tenant_isolation`` policy on its direct ``tenant_id`` column.
- ``action_findings`` (junction table, no tenant column): ENABLE + FORCE RLS
  with a ``tenant_isolation`` policy that resolves the tenant through the
  owning ``research_actions`` row. The action_id primary key makes the
  subquery fail-closed and index-accelerated; because ``research_actions.id``
  is a NOT NULL FK on this table, a bare junction row is impossible, so the
  policy column never evaluates against a missing parent.

PostgreSQL only, mirroring the surrounding RLS migrations (e.g.
``bb1c2d3e4f5a7``, ``e2f3a4b5c6d7``). No schema change on SQLite: dev/test
runs there are not tenant-isolation-critical; the FORCE-RLS matrix test pins
the exact protected set on PostgreSQL.

Revision ID: f6a7b8c9d0e1
Revises: f5a6b7c8d9e0
Create Date: 2026-09-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_ISOLATION = """
    current_setting('app.bypass_rls', true) = 'true'
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
"""

_JUNCTION_ISOLATION = """
    current_setting('app.bypass_rls', true) = 'true'
    OR EXISTS (
        SELECT 1 FROM research_actions ra
        WHERE ra.id = action_findings.action_id
          AND ra.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
    )
"""


def _protect(table: str, isolation: str) -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    bind.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({isolation}) WITH CHECK ({isolation})"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _protect("research_actions", _TENANT_ISOLATION)
    _protect("action_findings", _JUNCTION_ISOLATION)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in ("action_findings", "research_actions"):
        bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))