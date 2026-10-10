"""Add tenant workspace type for portal-only customer accounts.

Revision ID: fd2e3f4a5b67
Revises: fc1d2e3f4a56
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fd2e3f4a5b67"
down_revision: str | None = "fc1d2e3f4a56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("workspace_type", sa.String(20), nullable=False, server_default="full"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "workspace_type")
