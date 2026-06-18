"""add_stripe_fields_to_tenant

Revision ID: 41a9372f0044
Revises: 5a1b2c3d4e5f
Create Date: 2026-06-18 21:17:33.704279

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "41a9372f0044"
down_revision: Union[str, Sequence[str], None] = "5a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("stripe_customer_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "subscription_status",
                sa.String(length=50),
                nullable=False,
                server_default="incomplete",
            )
        )
        batch_op.add_column(
            sa.Column("current_period_end", sa.DateTime(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_tenants_stripe_customer_id"),
            ["stripe_customer_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tenants_stripe_customer_id"))
        batch_op.drop_column("current_period_end")
        batch_op.drop_column("subscription_status")
        batch_op.drop_column("stripe_subscription_id")
        batch_op.drop_column("stripe_customer_id")
