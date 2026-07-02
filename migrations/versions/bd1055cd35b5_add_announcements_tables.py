"""Add announcements and announcement_acks tables

Revision ID: bd1055cd35b5
Revises: 037cfd1d6ef7
Create Date: 2026-06-30 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bd1055cd35b5"
down_revision: Union[str, Sequence[str], None] = "037cfd1d6ef7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("severity", sa.String(20), default="info"),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column(
            "created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "announcement_acks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "announcement_id",
            sa.String(36),
            sa.ForeignKey("announcements.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("announcement_id", "user_id", name="uq_announcement_user"),
    )


def downgrade() -> None:
    op.drop_table("announcement_acks")
    op.drop_table("announcements")
