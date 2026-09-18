"""Preserve complete capture source URLs with finding screenshot evidence.

Revision ID: f3a4b5c6d9e0
Revises: f2a3b4c5d8e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f3a4b5c6d9e0"
down_revision: str | None = "f2a3b4c5d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("finding_screenshots") as batch_op:
        batch_op.alter_column(
            "source_url", existing_type=sa.String(length=500), type_=sa.String(length=2000)
        )


def downgrade() -> None:
    bind = op.get_bind()
    too_long = bind.execute(
        sa.text("SELECT count(*) FROM finding_screenshots WHERE length(source_url) > 500")
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            "Cannot safely downgrade finding_screenshots.source_url: values exceed 500 characters"
        )
    with op.batch_alter_table("finding_screenshots") as batch_op:
        batch_op.alter_column(
            "source_url", existing_type=sa.String(length=2000), type_=sa.String(length=500)
        )
