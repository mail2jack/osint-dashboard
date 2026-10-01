"""Enforce that API keys and their users share a tenant.

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d9e0
"""

from alembic import op
import sqlalchemy as sa


revision = "f4a5b6c7d8e9"
down_revision = "f3a4b5c6d9e0"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # SQLite is supported for local tests, but does not support adding these
    # composite constraints with ordinary ALTER TABLE. PostgreSQL is the
    # production database and receives the database-level invariant below;
    # SQLite continues to rely on the application-level checks.
    if connection.dialect.name == "sqlite":
        return

    mismatch_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM api_keys AS k
            JOIN users AS u ON u.id = k.user_id
            WHERE k.tenant_id IS DISTINCT FROM u.tenant_id
            """
        )
    ).scalar_one()
    if mismatch_count:
        raise RuntimeError(
            "Cannot add API-key tenant constraint: "
            f"{mismatch_count} existing key(s) have a tenant mismatch. "
            "Resolve these records explicitly before retrying the migration."
        )

    op.create_unique_constraint(
        "uq_users_id_tenant_id", "users", ["id", "tenant_id"]
    )
    op.create_foreign_key(
        "fk_api_keys_user_tenant",
        "api_keys",
        "users",
        ["user_id", "tenant_id"],
        ["id", "tenant_id"],
    )


def downgrade():
    # The upgrade intentionally does not add these constraints on SQLite;
    # mirror that behavior on downgrade so SQLite migration-cycle tests can
    # continue to the previous revision without unsupported ALTER syntax.
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_constraint("fk_api_keys_user_tenant", "api_keys", type_="foreignkey")
    op.drop_constraint("uq_users_id_tenant_id", "users", type_="unique")
