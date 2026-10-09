"""add intake owner to portal requests

Revision ID: fj0e1f2a3b45
Revises: fi9d0e1f2a34
"""

from alembic import op
import sqlalchemy as sa

revision = "fj0e1f2a3b45"
down_revision = "fi9d0e1f2a34"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("portal_requests", sa.Column("assigned_to_id", sa.String(length=36), nullable=True))
    op.create_index("ix_portal_requests_assigned_to_id", "portal_requests", ["assigned_to_id"])
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("portal_requests", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_portal_requests_assigned_to", "users", ["assigned_to_id"], ["id"]
            )
    else:
        op.create_foreign_key(
            "fk_portal_requests_assigned_to", "portal_requests", "users", ["assigned_to_id"], ["id"]
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("portal_requests", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_portal_requests_assigned_to", type_="foreignkey")
    else:
        op.drop_constraint("fk_portal_requests_assigned_to", "portal_requests", type_="foreignkey")
    op.drop_index("ix_portal_requests_assigned_to_id", table_name="portal_requests")
    op.drop_column("portal_requests", "assigned_to_id")
