"""Add finding_id FK column to social_accounts table.

Revision ID: c1d2e3f4a5b6
Revises: a4b5c6d7e8f9
Create Date: 2026-07-03 14:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "social_accounts",
        sa.Column("finding_id", sa.String(36), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_column("social_accounts", "finding_id")
