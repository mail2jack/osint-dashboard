"""multi-tenant: add tenant_id + RLS to all data tables

Revision ID: 4b160010d177
Revises: eb5f1d580af2
Create Date: 2026-06-09 12:00:00.000000

"""

from collections.abc import Sequence
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "4b160010d177"
down_revision: str | None = "eb5f1d580af2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All data tables that need tenant_id added
DATA_TABLES = [
    "addresses",
    "api_keys",
    "audit_logs",
    "cases",
    "clients",
    "comment_edit_history",
    "comments",
    "contacts",
    "document_templates",
    "documents",
    "financial_records",
    "findings",
    "invoice_items",
    "invoices",
    "login_logs",
    "notifications",
    "osint_searches",
    "payments",
    "phone_lookups",
    "reminders",
    "screenshots",
    "social_accounts",
    "spiderfoot_scans",
    "subjects",
    "users",
]

RLS_TABLES = [t for t in DATA_TABLES if t != "users"]


def _has_column(table: str, column: str) -> bool:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    conn = op.get_bind()

    # ========== CREATE NEW TABLES ==========

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("domain", sa.String(255), nullable=True, unique=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "tier", sa.String(20), nullable=False, server_default=sa.text("'free'")
        ),
        sa.Column("owner_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "platform_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("category", sa.String(50), server_default=sa.text("'general'")),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("is_encrypted", sa.Boolean, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("key", sa.String(100), nullable=False, index=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("category", sa.String(50), server_default=sa.text("'general'")),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("is_encrypted", sa.Boolean, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_key"),
    )

    # ========== SEED FIRST TENANT ==========

    seed_tenant_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO tenants (id, name, slug, is_active, tier, created_at, updated_at) "
            "VALUES (:id, :name, :slug, true, 'enterprise', :now, :now)"
        ),
        {
            "id": seed_tenant_id,
            "name": "Default Organization",
            "slug": "default",
            "now": datetime.now(timezone.utc),
        },
    )

    # ========== ADD tenant_id TO ALL DATA TABLES ==========

    for table in DATA_TABLES:
        if not _has_column(table, "tenant_id"):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column("tenant_id", sa.String(36), nullable=True)
                )
                batch_op.create_index(f"ix_{table}_tenant_id", ["tenant_id"])

    # ========== UPDATE EXISTING DATA ==========

    for table in DATA_TABLES:
        conn.execute(
            sa.text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
            {"tid": seed_tenant_id},
        )

    # ========== SET NOT NULL + ADD FK ==========

    for table in DATA_TABLES:
        if table == "users":
            continue  # users FK added separately (circular dep with Tenant.owner_id)
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("tenant_id", nullable=False)
            batch_op.create_foreign_key(
                f"fk_{table}_tenant", "tenants", ["tenant_id"], ["id"]
            )

    # Users FK after tenants exist
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("tenant_id", nullable=False)
        batch_op.create_foreign_key("fk_users_tenant", "tenants", ["tenant_id"], ["id"])

    # Tenant.owner_id FK
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.create_foreign_key("fk_tenants_owner", "users", ["owner_id"], ["id"])

    # ========== ADD is_super_admin ==========

    if not _has_column("users", "is_super_admin"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_super_admin",
                    sa.Boolean,
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )

    # ========== UPDATE ADMIN USER ==========

    conn.execute(
        sa.text(
            "UPDATE users SET is_super_admin = true, tenant_id = :tid "
            "WHERE role = 'admin'"
        ),
        {"tid": seed_tenant_id},
    )

    # ========== ENABLE ROW-LEVEL SECURITY (PostgreSQL only) ==========

    from sqlalchemy import inspect as sa_inspect

    dialect = sa_inspect(conn).dialect.name
    if dialect == "postgresql":
        for table in RLS_TABLES:
            conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(
                sa.text(f"""
                    CREATE POLICY tenant_isolation ON {table}
                    USING (tenant_id = current_setting('app.tenant_id')::text)
                """)
            )

    # ========== COPY SETTINGS TO PLATFORM_SETTINGS ==========

    if _has_column("settings", "key"):
        rows = conn.execute(
            sa.text(
                "SELECT key, value, category, description, is_encrypted FROM settings WHERE is_active = true"
            )
        ).fetchall()
        now_ts = datetime.now(timezone.utc)
        for row in rows:
            conn.execute(
                sa.text(
                    "INSERT INTO platform_settings (id, key, value, category, description, is_encrypted, created_at, updated_at) "
                    "VALUES (:id, :key, :value, :category, :description, :is_encrypted, :now, :now) "
                    "ON CONFLICT (key) DO NOTHING"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "key": row[0],
                    "value": row[1],
                    "category": row[2],
                    "description": row[3],
                    "is_encrypted": row[4],
                    "now": now_ts,
                },
            )


def downgrade() -> None:
    conn = op.get_bind()

    # ========== DISABLE RLS + DROP POLICIES ==========

    for table in RLS_TABLES:
        conn.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        conn.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    # ========== DROP tenant_id FROM ALL TABLES ==========

    for table in DATA_TABLES:
        if _has_column(table, "tenant_id"):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_constraint(f"fk_{table}_tenant", type_="foreignkey")
                batch_op.drop_index(f"ix_{table}_tenant_id")
                batch_op.drop_column("tenant_id")

    # ========== DROP is_super_admin ==========

    if _has_column("users", "is_super_admin"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("is_super_admin")

    # ========== DROP NEW TABLES ==========

    op.drop_table("tenant_settings")
    op.drop_table("platform_settings")
    op.drop_table("tenants")
