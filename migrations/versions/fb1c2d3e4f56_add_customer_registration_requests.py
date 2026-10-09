"""Add pre-approval customer registration requests.

Revision ID: fb1c2d3e4f56
Revises: fa0c1d2e3f45
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fb1c2d3e4f56"
down_revision: str | None = "fa0c1d2e3f45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_registration_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_name", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50)),
        sa.Column("message", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False, server_default="submitted"),
        sa.Column("reviewed_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("rejection_reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_customer_registration_requests_email", "customer_registration_requests", ["email"])
    op.create_index("ix_customer_registration_requests_status", "customer_registration_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_customer_registration_requests_status", table_name="customer_registration_requests")
    op.drop_index("ix_customer_registration_requests_email", table_name="customer_registration_requests")
    op.drop_table("customer_registration_requests")
