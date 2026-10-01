"""Persist source-bound AI narratives on investigations.

The narrative is an interpretation of findings, not evidence.  The source
findings remain authoritative and are not modified by this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b8c9d0e1f4a5"
down_revision: str | None = "a7b8c9d0e1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("investigations") as batch_op:
        batch_op.add_column(sa.Column("ai_narrative", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("ai_narrative_generated_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ai_narrative_generated_by", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ai_narrative_finding_count", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_investigations_ai_narrative_generated_by",
            "users",
            ["ai_narrative_generated_by"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("investigations") as batch_op:
        batch_op.drop_constraint(
            "fk_investigations_ai_narrative_generated_by", type_="foreignkey"
        )
        batch_op.drop_column("ai_narrative_finding_count")
        batch_op.drop_column("ai_narrative_generated_by")
        batch_op.drop_column("ai_narrative_generated_at")
        batch_op.drop_column("ai_narrative")
