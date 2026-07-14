"""increase audit_logs.action to varchar(50)

Revision ID: 0a2468d5d699
Revises: c1d2e3f4a5b6
Create Date: 2026-07-14 15:40:26.930389

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0a2468d5d699"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "action",
            existing_type=sa.VARCHAR(length=20),
            type_=sa.String(length=50),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "action",
            existing_type=sa.String(length=50),
            type_=sa.VARCHAR(length=20),
            existing_nullable=False,
        )
