"""add_usage_records_table

Revision ID: 40250c066c94
Revises: b2c3d4e5f6a7
Create Date: 2026-06-19 11:17:22.780098

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "40250c066c94"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=50), nullable=False),
        sa.Column("metric_value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "record_date",
            "metric_name",
            name="uq_usage_tenant_date_metric",
        ),
    )
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_usage_records_record_date"), ["record_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_usage_records_tenant_id"), ["tenant_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_usage_records_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_usage_records_record_date"))
    op.drop_table("usage_records")
