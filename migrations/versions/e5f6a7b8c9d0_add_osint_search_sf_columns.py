"""add spiderfoot_scan_id and sf_status to osint_searches

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-06-08 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    """Check if a column exists in a table (dialect-agnostic)."""
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _has_column("osint_searches", "spiderfoot_scan_id"):
        op.add_column(
            "osint_searches",
            sa.Column("spiderfoot_scan_id", sa.String(36), nullable=True),
        )
        op.create_index(
            "ix_osint_searches_spiderfoot_scan_id",
            "osint_searches",
            ["spiderfoot_scan_id"],
        )

    if not _has_column("osint_searches", "sf_status"):
        op.add_column(
            "osint_searches",
            sa.Column("sf_status", sa.String(20), nullable=True),
        )


def downgrade() -> None:
    if _has_column("osint_searches", "sf_status"):
        op.drop_column("osint_searches", "sf_status")
    if _has_column("osint_searches", "spiderfoot_scan_id"):
        op.drop_index("ix_osint_searches_spiderfoot_scan_id")
        op.drop_column("osint_searches", "spiderfoot_scan_id")
