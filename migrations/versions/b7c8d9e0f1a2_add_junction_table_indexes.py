"""add indexes on case_subjects.subject_id and case_assignments.user_id

Revision ID: b7c8d9e0f1a2
Revises: f1e2d3c4b5a6
Create Date: 2026-06-06 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f1e2d3c4b5a6"
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
    # Index on case_subjects.subject_id (query pattern: WHERE subject_id = ?)
    if _has_table("case_subjects") and not _has_index(
        "case_subjects", "ix_case_subjects_subject_id"
    ):
        with op.batch_alter_table("case_subjects", schema=None) as batch_op:
            batch_op.create_index(
                "ix_case_subjects_subject_id", ["subject_id"], unique=False
            )

    # Index on case_assignments.user_id (query pattern: WHERE user_id = ?)
    if _has_table("case_assignments") and not _has_index(
        "case_assignments", "ix_case_assignments_user_id"
    ):
        with op.batch_alter_table("case_assignments", schema=None) as batch_op:
            batch_op.create_index(
                "ix_case_assignments_user_id", ["user_id"], unique=False
            )

    # Index on AuditLog.case_id (query pattern: WHERE case_id = ?)
    if not _has_index("audit_logs", "ix_audit_logs_case_id"):
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.create_index("ix_audit_logs_case_id", ["case_id"], unique=False)


def downgrade() -> None:
    if _has_table("case_subjects") and _has_index(
        "case_subjects", "ix_case_subjects_subject_id"
    ):
        with op.batch_alter_table("case_subjects", schema=None) as batch_op:
            batch_op.drop_index("ix_case_subjects_subject_id")

    if _has_table("case_assignments") and _has_index(
        "case_assignments", "ix_case_assignments_user_id"
    ):
        with op.batch_alter_table("case_assignments", schema=None) as batch_op:
            batch_op.drop_index("ix_case_assignments_user_id")

    if _has_index("audit_logs", "ix_audit_logs_case_id"):
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.drop_index("ix_audit_logs_case_id")
