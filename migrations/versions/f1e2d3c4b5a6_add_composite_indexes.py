"""add composite indexes for common query patterns

Revision ID: f1e2d3c4b5a6
Revises: d4e5f6a7b8c9
Create Date: 2026-06-05 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


revision: str = "f1e2d3c4b5a6"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    indexes = [i["name"] for i in inspector.get_indexes(table)]
    return index_name in indexes


def upgrade() -> None:
    # Composite index for unread notification count queries
    if not _has_index("notifications", "ix_notifications_user_id_is_read"):
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
    if _has_index("notifications", "ix_notifications_user_id_is_read"):
        with op.batch_alter_table("notifications", schema=None) as batch_op:
            batch_op.drop_index("ix_notifications_user_id_is_read")

    if _has_index("audit_logs", "ix_audit_logs_case_id_entity_type"):
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.drop_index("ix_audit_logs_case_id_entity_type")

    if _has_index("reminders", "ix_reminders_assigned_to_is_completed"):
        with op.batch_alter_table("reminders", schema=None) as batch_op:
            batch_op.drop_index("ix_reminders_assigned_to_is_completed")
