"""Add phone_number to users, sms_enabled/whatsapp_enabled to notification_preferences

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-19 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("phone_number", sa.String(30), nullable=True))
    with op.batch_alter_table("notification_preferences") as batch_op:
        batch_op.add_column(sa.Column("sms_enabled", sa.Boolean(), default=False))
        batch_op.add_column(sa.Column("whatsapp_enabled", sa.Boolean(), default=False))


def downgrade() -> None:
    with op.batch_alter_table("notification_preferences") as batch_op:
        batch_op.drop_column("whatsapp_enabled")
        batch_op.drop_column("sms_enabled")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("phone_number")
