"""Link portal requests to the internal client record.

Revision ID: ff4a5b6c7d8e
Revises: fe3f4a5b6c7d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "ff4a5b6c7d8e"
down_revision: str | None = "fe3f4a5b6c7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("portal_requests", sa.Column("client_id", sa.String(36), nullable=True))
    op.create_index("ix_portal_requests_client_id", "portal_requests", ["client_id"])
    with op.batch_alter_table("portal_requests") as batch_op:
        batch_op.create_foreign_key(
            "fk_portal_requests_client_id", "clients", ["client_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_requests") as batch_op:
        batch_op.drop_constraint("fk_portal_requests_client_id", type_="foreignkey")
    op.drop_index("ix_portal_requests_client_id", table_name="portal_requests")
    op.drop_column("portal_requests", "client_id")
