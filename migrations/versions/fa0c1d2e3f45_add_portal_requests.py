"""Add tenant-scoped customer portal intake requests.

Revision ID: fa0c1d2e3f45
Revises: e6f7a8b9c0d1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fa0c1d2e3f45"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "current_setting('app.bypass_rls', true) = 'true' OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"


def upgrade() -> None:
    op.create_table(
        "portal_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("submitted_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_type", sa.String(50), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("context", sa.Text()),
        sa.Column("deadline", sa.String(200)),
        sa.Column("status", sa.String(30), nullable=False, server_default="submitted"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_portal_requests_tenant_id", "portal_requests", ["tenant_id"])
    op.create_index("ix_portal_requests_submitted_by_id", "portal_requests", ["submitted_by_id"])
    op.create_index("ix_portal_requests_status", "portal_requests", ["status"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("ALTER TABLE portal_requests ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE portal_requests FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("CREATE POLICY tenant_isolation ON portal_requests USING (" + _POLICY + ") WITH CHECK (" + _POLICY + ")"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON portal_requests"))
        bind.execute(sa.text("ALTER TABLE portal_requests NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE portal_requests DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_portal_requests_status", table_name="portal_requests")
    op.drop_index("ix_portal_requests_submitted_by_id", table_name="portal_requests")
    op.drop_index("ix_portal_requests_tenant_id", table_name="portal_requests")
    op.drop_table("portal_requests")
