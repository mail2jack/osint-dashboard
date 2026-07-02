"""Add breach_records table for GDPR Articles 33-34 compliance.

Revision ID: a4b5c6d7e8f9
Revises: f3e4d5c6b7a8
Create Date: 2026-07-02 14:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3e4d5c6b7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "breach_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("breach_type", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("data_affected", sa.Text(), nullable=True),
        sa.Column("affected_count", sa.Integer(), nullable=True),
        sa.Column(
            "risk_level", sa.String(20), nullable=False, server_default="unknown"
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="open", index=True
        ),
        sa.Column(
            "authority_notified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("authority_notified_at", sa.DateTime(), nullable=True),
        sa.Column("authority_notes", sa.Text(), nullable=True),
        sa.Column(
            "subjects_notified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("subjects_notified_at", sa.DateTime(), nullable=True),
        sa.Column("subject_communication", sa.Text(), nullable=True),
        sa.Column("remedial_actions", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("breach_records")
