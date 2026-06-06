"""add bank_account column to subjects table

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-06-06 17:05:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    cols = [c["name"] for c in inspector.get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("subjects", "bank_account"):
        op.add_column(
            "subjects",
            sa.Column("bank_account", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    if _has_column("subjects", "bank_account"):
        op.drop_column("subjects", "bank_account")
