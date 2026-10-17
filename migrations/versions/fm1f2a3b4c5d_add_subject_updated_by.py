"""Track the last user who edited a subject.

Revision ID: fm1f2a3b4c5d
Revises: fl0e1f2a3b46
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fm1f2a3b4c5d"
down_revision: str | None = "fl0e1f2a3b46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subjects") as batch:
        batch.add_column(sa.Column("updated_by", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_subjects_updated_by_users",
            "users",
            ["updated_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_subjects_updated_by", ["updated_by"])

    op.execute(
        sa.text(
            "UPDATE subjects SET updated_by = created_by "
            "WHERE updated_by IS NULL AND created_by IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("subjects") as batch:
        batch.drop_index("ix_subjects_updated_by")
        batch.drop_constraint("fk_subjects_updated_by_users", type_="foreignkey")
        batch.drop_column("updated_by")
