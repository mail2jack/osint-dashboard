"""Add duplicate marker to portal requests.

Revision ID: ff6a7b8c9d01
Revises: fe5f6a7b8c90
"""

from alembic import context, op
import sqlalchemy as sa


revision = "ff6a7b8c9d01"
down_revision = "fe5f6a7b8c90"
branch_labels = None
depends_on = None


def upgrade():
    if context.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("portal_requests", recreate="always") as batch_op:
            batch_op.add_column(
                sa.Column("duplicate_of_id", sa.String(length=36), nullable=True)
            )
            batch_op.create_index(
                "ix_portal_requests_duplicate_of_id", ["duplicate_of_id"]
            )
            batch_op.create_foreign_key(
                "fk_portal_requests_duplicate_of", "portal_requests", ["duplicate_of_id"], ["id"]
            )
    else:
        op.add_column(
            "portal_requests",
            sa.Column("duplicate_of_id", sa.String(length=36), nullable=True),
        )
        op.create_index(
            "ix_portal_requests_duplicate_of_id",
            "portal_requests",
            ["duplicate_of_id"],
        )
        op.create_foreign_key(
            "fk_portal_requests_duplicate_of",
            "portal_requests",
            "portal_requests",
            ["duplicate_of_id"],
            ["id"],
        )


def downgrade():
    if context.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("portal_requests", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_portal_requests_duplicate_of", type_="foreignkey")
            batch_op.drop_index("ix_portal_requests_duplicate_of_id")
            batch_op.drop_column("duplicate_of_id")
    else:
        op.drop_constraint(
            "fk_portal_requests_duplicate_of", "portal_requests", type_="foreignkey"
        )
        op.drop_index("ix_portal_requests_duplicate_of_id", table_name="portal_requests")
        op.drop_column("portal_requests", "duplicate_of_id")
