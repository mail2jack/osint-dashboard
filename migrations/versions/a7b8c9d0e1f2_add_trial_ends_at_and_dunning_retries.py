"""Add trial_ends_at and dunning_retries to Tenant

Revision ID: a7b8c9d0e1f2
Revises: 6612af40fc2d
Create Date: 2026-06-19 12:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "6612af40fc2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("trial_ends_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("dunning_retries", sa.Integer(), server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("dunning_retries")
        batch_op.drop_column("trial_ends_at")
