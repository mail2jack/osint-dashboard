"""Add registration type to customer registration requests.

Revision ID: fc1d2e3f4a56
Revises: fb1c2d3e4f56
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fc1d2e3f4a56"
down_revision: str | None = "fb1c2d3e4f56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_registration_requests",
        sa.Column("registration_type", sa.String(30), nullable=False, server_default="one_off"),
    )
    op.create_index(
        "ix_customer_registration_requests_registration_type",
        "customer_registration_requests",
        ["registration_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_registration_requests_registration_type",
        table_name="customer_registration_requests",
    )
    op.drop_column("customer_registration_requests", "registration_type")
