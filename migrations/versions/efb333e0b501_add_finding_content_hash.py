"""add content_hash column to findings table

Revision ID: b8c9d0e1f2a3
Revises: 0a2468d5d699
Create Date: 2026-07-21 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "efb333e0b501"
down_revision: Union[str, Sequence[str], None] = "0a2468d5d699"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("content_hash", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_column("content_hash")
