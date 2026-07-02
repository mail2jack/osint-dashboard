"""Add DPA records table for Article 28 GDPR compliance.

Revision ID: f3e4d5c6b7a8
Revises: bd1055cd35b5
Create Date: 2026-07-02 12:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "f3e4d5c6b7a8"
down_revision: Union[str, None] = "505602a5fa7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dpa_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, index=True),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("data_categories", sa.String(500), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("transfer_safeguard", sa.String(200), nullable=True),
        sa.Column("contract_date", sa.Date(), nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="active", index=True
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("dpa_records")
