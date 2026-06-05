"""add notifications table + composite indexes for common query patterns

Revision ID: f1e2d3c4b5a6
Revises: d4e5f6a7b8c9
Create Date: 2026-06-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "f1e2d3c4b5a6"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    return table in inspector.get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    bind = op.get_context().bind
    inspector = inspect(bind)
    indexes = [i["name"] for i in inspector.get_indexes(table)]
    return index_name in indexes


def upgrade() -> None:
    # Create notifications table if it doesn't exist yet
    if not _has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("message", sa.String(length=500), nullable=False),
            sa.Column("link", sa.String(length=500), nullable=True),
            sa.Column("is_read", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("notifications", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_notifications_user_id"), ["user_id"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_notifications_is_read"), ["is_read"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_notifications_created_at"), ["created_at"], unique=False
            )

    # Composite index for unread notification count queries
    if _has_table("notifications") and not _has_index(
        "notifications", "ix_notifications_user_id_is_read"
    ):
        with op.batch_alter_table("notifications", schema=None) as batch_op:
            batch_op.create_index(
                "ix_notifications_user_id_is_read", ["user_id", "is_read"], unique=False
            )

    # Composite index for audit log queries filtering by case + entity type
    if not _has_index("audit_logs", "ix_audit_logs_case_id_entity_type"):
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.create_index(
                "ix_audit_logs_case_id_entity_type",
                ["case_id", "entity_type"],
                unique=False,
            )

    # Composite index for reminder queries filtering by user + completion status
    if not _has_index("reminders", "ix_reminders_assigned_to_is_completed"):
        with op.batch_alter_table("reminders", schema=None) as batch_op:
            batch_op.create_index(
                "ix_reminders_assigned_to_is_completed",
                ["assigned_to", "is_completed"],
                unique=False,
            )


def downgrade() -> None:
    if _has_table("notifications") and _has_index(
        "notifications", "ix_notifications_user_id_is_read"
    ):
        with op.batch_alter_table("notifications", schema=None) as batch_op:
            batch_op.drop_index("ix_notifications_user_id_is_read")

    if _has_index("audit_logs", "ix_audit_logs_case_id_entity_type"):
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.drop_index("ix_audit_logs_case_id_entity_type")

    if _has_index("reminders", "ix_reminders_assigned_to_is_completed"):
        with op.batch_alter_table("reminders", schema=None) as batch_op:
            batch_op.drop_index("ix_reminders_assigned_to_is_completed")
