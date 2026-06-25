"""Add Notification category/title + NotificationPreference

Revision ID: 6612af40fc2d
Revises: 40250c066c94
Create Date: 2026-06-19 12:29:34.523132

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6612af40fc2d"
down_revision: Union[str, Sequence[str], None] = "40250c066c94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("web_enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "category", name="uq_user_notification_category"
        ),
    )
    op.create_index(
        op.f("ix_notification_preferences_user_id"),
        "notification_preferences",
        ["user_id"],
    )

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(
            sa.Column("category", sa.String(50), server_default="general")
        )
        batch_op.add_column(sa.Column("title", sa.String(200), server_default=""))
        batch_op.create_index(op.f("ix_notifications_category"), ["category"])

    op.execute("UPDATE notifications SET category='general' WHERE category IS NULL")
    op.execute("UPDATE notifications SET title='' WHERE title IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_index(op.f("ix_notifications_category"))
        batch_op.drop_column("title")
        batch_op.drop_column("category")

    op.drop_index(
        op.f("ix_notification_preferences_user_id"),
        table_name="notification_preferences",
    )
    op.drop_table("notification_preferences")
