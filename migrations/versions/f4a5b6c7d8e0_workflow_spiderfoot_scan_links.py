"""Link SpiderFoot scan records to workflow actions and investigations.

Legacy SpiderFoot records remain valid: both columns are nullable.  Native
workflow source research uses the links to retain the existing tenant/case
scope, action audit trail and investigation context without duplicating a
second scan table.  The existing FORCE-RLS policy on ``spiderfoot_scans``
continues to protect the added references.

Revision ID: f4a5b6c7d8e0
Revises: f3a4b5c6d9e0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f4a5b6c7d8e0"
down_revision: str | None = "f3a4b5c6d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table keeps the SQLite test migration path equivalent to
    # PostgreSQL while preserving the existing RLS-enabled PostgreSQL table.
    with op.batch_alter_table("spiderfoot_scans") as batch_op:
        batch_op.add_column(sa.Column("investigation_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("research_action_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_spiderfoot_scans_investigation_id",
            "investigations",
            ["investigation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_spiderfoot_scans_research_action_id",
            "research_actions",
            ["research_action_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_spiderfoot_scans_investigation_id", ["investigation_id"]
        )
        batch_op.create_unique_constraint(
            "uq_spiderfoot_scans_research_action_id", ["research_action_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("spiderfoot_scans") as batch_op:
        batch_op.drop_constraint(
            "uq_spiderfoot_scans_research_action_id", type_="unique"
        )
        batch_op.drop_index("ix_spiderfoot_scans_investigation_id")
        batch_op.drop_constraint(
            "fk_spiderfoot_scans_research_action_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_spiderfoot_scans_investigation_id", type_="foreignkey"
        )
        batch_op.drop_column("research_action_id")
        batch_op.drop_column("investigation_id")
