"""Add client-scoped portal access and forced password changes.

Revision ID: fe3f4a5b6c7d
Revises: fd2e3f4a5b67
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fe3f4a5b6c7d"
down_revision: str | None = "fd2e3f4a5b67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = (
    "current_setting('app.bypass_rls', true) = 'true' "
    "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
)


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("portal_access_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("invitations", sa.Column("client_id", sa.String(36), nullable=True))
    op.add_column(
        "invitations",
        sa.Column("is_portal_invitation", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_invitations_client_id", "invitations", ["client_id"])
    op.create_index("ix_invitations_is_portal_invitation", "invitations", ["is_portal_invitation"])
    with op.batch_alter_table("invitations") as batch_op:
        batch_op.create_foreign_key(
            "fk_invitations_client_id", "clients", ["client_id"], ["id"]
        )

    op.create_table(
        "client_portal_access",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="portal_viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("invited_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "client_id", "user_id", name="uq_client_portal_access_user"),
    )
    op.create_index("ix_client_portal_access_tenant_id", "client_portal_access", ["tenant_id"])
    op.create_index("ix_client_portal_access_client_id", "client_portal_access", ["client_id"])
    op.create_index("ix_client_portal_access_user_id", "client_portal_access", ["user_id"])
    op.create_index("ix_client_portal_access_user_active", "client_portal_access", ["user_id", "is_active"])
    op.create_index("ix_client_portal_access_tenant_client", "client_portal_access", ["tenant_id", "client_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE client_portal_access ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE client_portal_access FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY tenant_isolation ON client_portal_access USING ("
            + _POLICY
            + ") WITH CHECK ("
            + _POLICY
            + ")"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON client_portal_access")
        op.execute("ALTER TABLE client_portal_access NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE client_portal_access DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_client_portal_access_tenant_client", table_name="client_portal_access")
    op.drop_index("ix_client_portal_access_user_active", table_name="client_portal_access")
    op.drop_index("ix_client_portal_access_user_id", table_name="client_portal_access")
    op.drop_index("ix_client_portal_access_client_id", table_name="client_portal_access")
    op.drop_index("ix_client_portal_access_tenant_id", table_name="client_portal_access")
    op.drop_table("client_portal_access")
    with op.batch_alter_table("invitations") as batch_op:
        batch_op.drop_constraint("fk_invitations_client_id", type_="foreignkey")
    op.drop_index("ix_invitations_is_portal_invitation", table_name="invitations")
    op.drop_index("ix_invitations_client_id", table_name="invitations")
    op.drop_column("invitations", "is_portal_invitation")
    op.drop_column("invitations", "client_id")
    op.drop_column("users", "must_change_password")
    op.drop_column("clients", "portal_access_enabled")
