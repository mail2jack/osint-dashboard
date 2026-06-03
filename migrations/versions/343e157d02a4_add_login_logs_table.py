"""add login_logs table

Revision ID: 343e157d02a4
Revises: 8255fef205fa
Create Date: 2026-05-30 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "343e157d02a4"
down_revision: str | Sequence[str] | None = "8255fef205fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "login_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("isp", sa.String(length=200), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("is_success", sa.Boolean(), nullable=True),
        sa.Column("is_anomaly", sa.Boolean(), nullable=True),
        sa.Column("anomaly_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("login_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_login_logs_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_login_logs_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_login_logs_user_created"),
            ["user_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("login_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_logs_user_created"))
        batch_op.drop_index(batch_op.f("ix_login_logs_created_at"))
        batch_op.drop_index(batch_op.f("ix_login_logs_user_id"))
    op.drop_table("login_logs")
