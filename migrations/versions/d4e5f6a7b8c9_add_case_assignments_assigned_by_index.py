"""add missing index on case_assignments.assigned_by

Revision ID: d4e5f6a7b8c9
Revises: a1b2c3d4e5f6
Create Date: 2026-06-01 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    indexes = [i["name"] for i in inspector.get_indexes(table)]
    return index_name in indexes


def upgrade() -> None:
    if not _has_index("case_assignments", "ix_case_assignments_assigned_by"):
        with op.batch_alter_table("case_assignments", schema=None) as batch_op:
            batch_op.create_index(
                "ix_case_assignments_assigned_by", ["assigned_by"], unique=False
            )


def downgrade() -> None:
    if _has_index("case_assignments", "ix_case_assignments_assigned_by"):
        with op.batch_alter_table("case_assignments", schema=None) as batch_op:
            batch_op.drop_index("ix_case_assignments_assigned_by")
