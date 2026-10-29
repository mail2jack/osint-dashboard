"""Store encrypted email forensic reports for customer registrations.

Revision ID: fo1f2a3b4c5d
Revises: fn2a3b4c5d6e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fo1f2a3b4c5d"
down_revision: str | None = "fn2a3b4c5d6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_registration_mail_analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("registration_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("verdict", sa.String(length=40), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("raw_message_encrypted", sa.Text(), nullable=False),
        sa.Column("message_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["registration_id"], ["customer_registration_requests.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customer_registration_mail_analyses_registration_id", "customer_registration_mail_analyses", ["registration_id"])
    op.create_index("ix_customer_registration_mail_analyses_tenant_id", "customer_registration_mail_analyses", ["tenant_id"])
    op.create_index("ix_customer_registration_mail_analyses_created_by_id", "customer_registration_mail_analyses", ["created_by_id"])
    op.create_index("ix_customer_registration_mail_analyses_message_sha256", "customer_registration_mail_analyses", ["message_sha256"])
    op.create_index("ix_customer_registration_mail_analyses_created_at", "customer_registration_mail_analyses", ["created_at"])


def downgrade() -> None:
    op.drop_table("customer_registration_mail_analyses")
