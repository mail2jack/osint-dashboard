"""Add audit metadata columns to research_actions and finding_screenshots

Revision ID: 505602a5fa7d
Revises: 482d3b344d9f
Create Date: 2026-07-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "505602a5fa7d"
down_revision: str | Sequence[str] | None = "482d3b344d9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    return table in inspector.get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    bind = op.get_context().bind
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    # research_actions: add updated_at, created_by
    if _has_table("research_actions"):
        if not _has_column("research_actions", "updated_at"):
            with op.batch_alter_table("research_actions") as batch_op:
                batch_op.add_column(
                    sa.Column("updated_at", sa.DateTime(), nullable=True)
                )
        if not _has_column("research_actions", "created_by"):
            with op.batch_alter_table("research_actions") as batch_op:
                batch_op.add_column(
                    sa.Column("created_by", sa.String(36), nullable=True, index=True)
                )
                batch_op.create_foreign_key(
                    "fk_research_actions_created_by",
                    "users",
                    ["created_by"],
                    ["id"],
                )

    # finding_screenshots: add created_at, created_by
    if _has_table("finding_screenshots"):
        if not _has_column("finding_screenshots", "created_at"):
            with op.batch_alter_table("finding_screenshots") as batch_op:
                batch_op.add_column(
                    sa.Column("created_at", sa.DateTime(), nullable=True)
                )
        if not _has_column("finding_screenshots", "created_by"):
            with op.batch_alter_table("finding_screenshots") as batch_op:
                batch_op.add_column(
                    sa.Column("created_by", sa.String(36), nullable=True, index=True)
                )
                batch_op.create_foreign_key(
                    "fk_finding_screenshots_created_by",
                    "users",
                    ["created_by"],
                    ["id"],
                )


def downgrade() -> None:
    if _has_table("research_actions"):
        if _has_column("research_actions", "updated_at"):
            with op.batch_alter_table("research_actions") as batch_op:
                batch_op.drop_column("updated_at")
        if _has_column("research_actions", "created_by"):
            with op.batch_alter_table("research_actions") as batch_op:
                batch_op.drop_column("created_by")

    if _has_table("finding_screenshots"):
        if _has_column("finding_screenshots", "created_at"):
            with op.batch_alter_table("finding_screenshots") as batch_op:
                batch_op.drop_column("created_at")
        if _has_column("finding_screenshots", "created_by"):
            with op.batch_alter_table("finding_screenshots") as batch_op:
                batch_op.drop_column("created_by")
