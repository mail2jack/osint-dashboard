"""add secure customer/staff case chat"""
from alembic import op
import sqlalchemy as sa

revision = "fh8c9d0e1f23"
down_revision = "fg7b8c9d0e12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "case_messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("sender_user_id", sa.String(36), nullable=False),
        sa.Column("sender_role", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_case_messages_tenant_id", "case_messages", ["tenant_id"])
    op.create_index("ix_case_messages_case_id", "case_messages", ["case_id"])
    op.create_index("ix_case_messages_sender_user_id", "case_messages", ["sender_user_id"])
    op.create_index("ix_case_messages_is_read", "case_messages", ["is_read"])
    op.create_index("ix_case_messages_created_at", "case_messages", ["created_at"])


def downgrade():
    for name in ("ix_case_messages_created_at", "ix_case_messages_is_read", "ix_case_messages_sender_user_id", "ix_case_messages_case_id", "ix_case_messages_tenant_id"):
        op.drop_index(name, table_name="case_messages")
    op.drop_table("case_messages")
