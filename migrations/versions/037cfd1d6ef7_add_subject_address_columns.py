"""add_subject_address_columns

Revision ID: 037cfd1d6ef7
Revises: 26519d16f4f2
Create Date: 2026-06-25 14:42:15.256966

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "037cfd1d6ef7"
down_revision: Union[str, Sequence[str], None] = "26519d16f4f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subjects") as batch_op:
        batch_op.add_column(sa.Column("street", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("house_number", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("house_number_addition", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("postal_code", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(sa.Column("city", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("subjects") as batch_op:
        batch_op.drop_column("city")
        batch_op.drop_column("postal_code")
        batch_op.drop_column("house_number_addition")
        batch_op.drop_column("house_number")
        batch_op.drop_column("street")
