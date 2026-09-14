"""Add FeatureFlag actor attribution and FORCE RLS.

Revision ID: f8a9b0c1d2e3
Revises: f6a7b8c9d0e1
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = """
    current_setting('app.bypass_rls', true) = 'true'
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
"""


def upgrade() -> None:
    with op.batch_alter_table("feature_flags") as batch:
        batch.add_column(sa.Column("created_by_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("updated_by_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_feature_flags_created_by_id_users",
            "users",
            ["created_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_feature_flags_updated_by_id_users",
            "users",
            ["updated_by_id"],
            ["id"],
            ondelete="SET NULL",
        )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("ALTER TABLE feature_flags ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE feature_flags FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON feature_flags"))
        bind.execute(
            sa.text(
                "CREATE POLICY tenant_isolation ON feature_flags "
                f"USING ({_POLICY}) WITH CHECK ({_POLICY})"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON feature_flags"))
        bind.execute(sa.text("ALTER TABLE feature_flags NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE feature_flags DISABLE ROW LEVEL SECURITY"))
    with op.batch_alter_table("feature_flags") as batch:
        batch.drop_constraint(
            "fk_feature_flags_updated_by_id_users", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_feature_flags_created_by_id_users", type_="foreignkey"
        )
        batch.drop_column("updated_by_id")
        batch.drop_column("created_by_id")
