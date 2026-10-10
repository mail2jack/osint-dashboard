"""Link converted portal requests to their case.

Revision ID: fc3d4e5f6a78
Revises: fb2c3d4e5f67
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fc3d4e5f6a78"
down_revision: str | None = "fb2c3d4e5f67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("portal_requests", sa.Column("case_id", sa.String(36), nullable=True))
    op.create_index("ix_portal_requests_case_id", "portal_requests", ["case_id"])
    with op.batch_alter_table("portal_requests") as batch_op:
        batch_op.create_foreign_key(
            "fk_portal_requests_case_id", "cases", ["case_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_requests") as batch_op:
        batch_op.drop_constraint("fk_portal_requests_case_id", type_="foreignkey")
    op.drop_index("ix_portal_requests_case_id", table_name="portal_requests")
    op.drop_column("portal_requests", "case_id")
