"""Allow investigators to reject screenshot evidence without deleting its audit row.

Revision ID: fn2a3b4c5d6e
Revises: fm1f2a3b4c5d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fn2a3b4c5d6e"
down_revision: str | None = "fm1f2a3b4c5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("finding_screenshots") as batch:
        batch.add_column(sa.Column("rejected_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("rejected_by", sa.String(36), nullable=True))
        batch.add_column(sa.Column("rejection_reason", sa.String(500), nullable=True))
        batch.create_foreign_key(
            "fk_finding_screenshots_rejected_by_users",
            "users",
            ["rejected_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_finding_screenshots_rejected_by", ["rejected_by"])


def downgrade() -> None:
    with op.batch_alter_table("finding_screenshots") as batch:
        batch.drop_index("ix_finding_screenshots_rejected_by")
        batch.drop_constraint(
            "fk_finding_screenshots_rejected_by_users", type_="foreignkey"
        )
        batch.drop_column("rejection_reason")
        batch.drop_column("rejected_by")
        batch.drop_column("rejected_at")
