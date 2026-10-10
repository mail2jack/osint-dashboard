"""Link documents to portal requests.

Revision ID: fe5f6a7b8c90
Revises: fd4e5f6a7b89
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fe5f6a7b8c90"
down_revision: str | None = "fd4e5f6a7b89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("portal_request_id", sa.String(36), nullable=True))
    op.create_index("ix_documents_portal_request_id", "documents", ["portal_request_id"])
    with op.batch_alter_table("documents") as batch_op:
        batch_op.create_foreign_key(
            "fk_documents_portal_request_id", "portal_requests", ["portal_request_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_portal_request_id", type_="foreignkey")
    op.drop_index("ix_documents_portal_request_id", table_name="documents")
    op.drop_column("documents", "portal_request_id")
