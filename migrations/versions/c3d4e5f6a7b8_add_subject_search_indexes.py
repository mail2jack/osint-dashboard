"""add composite indexes for subject search performance

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-20 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    try:
        indexes = [i["name"] for i in inspector.get_indexes(table)]
    except Exception:
        return False
    return index_name in indexes


def upgrade() -> None:
    # Composite index for tenant isolation queries (most common filter pattern)
    if not _has_index("subjects", "ix_subjects_tenant_id_is_deleted"):
        with op.batch_alter_table("subjects", schema=None) as batch_op:
            batch_op.create_index(
                "ix_subjects_tenant_id_is_deleted",
                ["tenant_id", "is_deleted"],
                unique=False,
            )

    # Composite index for name search within tenant
    if not _has_index("subjects", "ix_subjects_tenant_id_name"):
        with op.batch_alter_table("subjects", schema=None) as batch_op:
            batch_op.create_index(
                "ix_subjects_tenant_id_name",
                ["tenant_id", "name"],
                unique=False,
            )

    # Composite index for client tenant isolation
    if not _has_index("clients", "ix_clients_tenant_id_is_deleted"):
        with op.batch_alter_table("clients", schema=None) as batch_op:
            batch_op.create_index(
                "ix_clients_tenant_id_is_deleted",
                ["tenant_id", "is_deleted"],
                unique=False,
            )

    # Composite index for case tenant isolation
    if not _has_index("cases", "ix_cases_tenant_id_is_deleted"):
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.create_index(
                "ix_cases_tenant_id_is_deleted",
                ["tenant_id", "is_deleted"],
                unique=False,
            )


def downgrade() -> None:
    if _has_index("subjects", "ix_subjects_tenant_id_is_deleted"):
        with op.batch_alter_table("subjects", schema=None) as batch_op:
            batch_op.drop_index("ix_subjects_tenant_id_is_deleted")

    if _has_index("subjects", "ix_subjects_tenant_id_name"):
        with op.batch_alter_table("subjects", schema=None) as batch_op:
            batch_op.drop_index("ix_subjects_tenant_id_name")

    if _has_index("clients", "ix_clients_tenant_id_is_deleted"):
        with op.batch_alter_table("clients", schema=None) as batch_op:
            batch_op.drop_index("ix_clients_tenant_id_is_deleted")

    if _has_index("cases", "ix_cases_tenant_id_is_deleted"):
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.drop_index("ix_cases_tenant_id_is_deleted")
