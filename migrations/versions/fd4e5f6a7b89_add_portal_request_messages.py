"""Add customer follow-up messages to portal requests.

Revision ID: fd4e5f6a7b89
Revises: fc3d4e5f6a78
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fd4e5f6a7b89"
down_revision: str | None = "fc3d4e5f6a78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portal_request_messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("author_user_id", sa.String(36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["portal_requests.id"]),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portal_request_messages_tenant_id", "portal_request_messages", ["tenant_id"])
    op.create_index("ix_portal_request_messages_request_id", "portal_request_messages", ["request_id"])
    op.create_index("ix_portal_request_messages_author_user_id", "portal_request_messages", ["author_user_id"])
    op.create_index("ix_portal_request_messages_created_at", "portal_request_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_portal_request_messages_created_at", table_name="portal_request_messages")
    op.drop_index("ix_portal_request_messages_author_user_id", table_name="portal_request_messages")
    op.drop_index("ix_portal_request_messages_request_id", table_name="portal_request_messages")
    op.drop_index("ix_portal_request_messages_tenant_id", table_name="portal_request_messages")
    op.drop_table("portal_request_messages")
