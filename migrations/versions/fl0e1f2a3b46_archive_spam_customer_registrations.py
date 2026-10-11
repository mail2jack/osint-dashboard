"""Add soft-delete timestamp for spam customer registrations.

Revision ID: fl0e1f2a3b46
Revises: fk0e1f2a3b46
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fl0e1f2a3b46"
down_revision: str | None = "fk0e1f2a3b46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_registration_requests",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_customer_registration_requests_archived_at",
        "customer_registration_requests",
        ["archived_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_registration_requests_archived_at",
        table_name="customer_registration_requests",
    )
    op.drop_column("customer_registration_requests", "archived_at")
