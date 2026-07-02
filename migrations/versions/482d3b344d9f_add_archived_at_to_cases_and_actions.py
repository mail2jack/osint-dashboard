"""Add archived_at to cases and research_actions tables

Revision ID: 482d3b344d9f
Revises: bd1055cd35b5
Create Date: 2026-07-01 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "482d3b344d9f"
down_revision: Union[str, Sequence[str], None] = "bd1055cd35b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cases", sa.Column("archived_at", sa.DateTime(), nullable=True, index=True)
    )
    op.add_column(
        "research_actions",
        sa.Column("archived_at", sa.DateTime(), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_column("research_actions", "archived_at")
    op.drop_column("cases", "archived_at")
