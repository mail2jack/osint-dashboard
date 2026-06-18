"""add missing indexes on tenant.owner_id and tenant_settings.tenant_id

Revision ID: 50c07e49855b
Revises: d8a9431786e3
Create Date: 2026-06-18 14:42:00.000000
"""

from typing import Sequence
from alembic import op

revision: str = "50c07e49855b"
down_revision: str | Sequence[str] | None = "d8a9431786e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_tenants_owner_id"), "tenants", ["owner_id"], unique=False)
    op.create_index(
        op.f("ix_tenant_settings_tenant_id"),
        "tenant_settings",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tenants_owner_id"), table_name="tenants")
    op.drop_index(op.f("ix_tenant_settings_tenant_id"), table_name="tenant_settings")
