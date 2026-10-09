"""Allow customer registration requests to be assigned to a tenant.

Revision ID: fb2c3d4e5f67
Revises: ff4a5b6c7d8e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fb2c3d4e5f67"
down_revision: str | None = "ff4a5b6c7d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_registration_requests",
        sa.Column("tenant_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_customer_registration_requests_tenant_id",
        "customer_registration_requests",
        ["tenant_id"],
    )
    with op.batch_alter_table("customer_registration_requests") as batch_op:
        batch_op.create_foreign_key(
            "fk_customer_registration_requests_tenant_id",
            "tenants",
            ["tenant_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("customer_registration_requests") as batch_op:
        batch_op.drop_constraint(
            "fk_customer_registration_requests_tenant_id", type_="foreignkey"
        )
    op.drop_index(
        "ix_customer_registration_requests_tenant_id",
        table_name="customer_registration_requests",
    )
    op.drop_column("customer_registration_requests", "tenant_id")
