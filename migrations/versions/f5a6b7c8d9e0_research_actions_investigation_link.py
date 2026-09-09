"""research_actions.investigation_id + same-case/tenant parent-key (ADR-0005)

Adds a nullable ``investigation_id`` to ``research_actions``:

- ``NULL`` → the action is **case-wide by explicit semantics** (existing
  behavior, unchanged).
- ``NOT NULL`` → the action is bound to that investigation, and the
  investigation must belong to the **same case and tenant** as the action.

Enforced at the database level (ADR-0005 D2/D3, Option A): a composite FK on
``research_actions(investigation_id, case_id, tenant_id)`` references the
unique parent key ``investigations(id, case_id, tenant_id)``. PostgreSQL
raises with the ``foreign_key_violation`` SQLSTATE (23503); SQLite refuses the
row whenever ``PRAGMA foreign_keys`` is on. The invariant therefore also
holds when RLS is bypassed, mirroring ADR-0002 D8.

No backfill (ADR-0005 D4): existing rows stay ``NULL`` (= case-wide)
indefinitely; a new link is always a deliberate, audit-logged write made by
PR-B/API code — never by this migration.

Revision ID: f5a6b7c8d9e0
Revises: e2f3a4b5c6d7
Create Date: 2026-09-08

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_parent_key() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.batch_alter_table("investigations") as batch_op:
            batch_op.create_unique_constraint(
                "uq_investigations_id_case_tenant",
                ["id", "case_id", "tenant_id"],
            )
    else:
        # SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``. A plain unique
        # index is the matching parent key for the composite FK below and it
        # does NOT rebuild the table, so the case_id/tenant_id immutability
        # triggers created by dd1e2f3a4b5c7 survive the upgrade.
        op.create_index(
            "uq_investigations_id_case_tenant",
            "investigations",
            ["id", "case_id", "tenant_id"],
            unique=True,
        )


def _drop_parent_key() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.batch_alter_table("investigations") as batch_op:
            batch_op.drop_constraint(
                "uq_investigations_id_case_tenant", type_="unique"
            )
    else:
        op.drop_index("uq_investigations_id_case_tenant", table_name="investigations")


def upgrade() -> None:
    # 1) Unique parent key on investigations so the composite FK below has a
    #    matching candidate key on both PostgreSQL and SQLite.
    _add_parent_key()

    # 2) Nullable link column + composite FK on research_actions. NULL stays
    #    valid (case-wide); a non-NULL investigation_id must point to an
    #    investigation with the exact same case_id and tenant_id.
    with op.batch_alter_table("research_actions") as batch_op:
        batch_op.add_column(
            sa.Column("investigation_id", sa.String(36), nullable=True)
        )
        batch_op.create_index(
            "ix_research_actions_investigation_id", ["investigation_id"]
        )
        batch_op.create_foreign_key(
            "fk_research_actions_investigation_case_tenant",
            "investigations",
            ["investigation_id", "case_id", "tenant_id"],
            ["id", "case_id", "tenant_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("research_actions") as batch_op:
        batch_op.drop_constraint(
            "fk_research_actions_investigation_case_tenant", type_="foreignkey"
        )
        batch_op.drop_index("ix_research_actions_investigation_id")
        batch_op.drop_column("investigation_id")

    _drop_parent_key()