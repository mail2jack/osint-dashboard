"""enforce tenant context for PostgreSQL RLS"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d9e8f7a6b5c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = [
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
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (
                    current_setting('app.bypass_rls', true) = 'true'
                    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
                )
                WITH CHECK (
                    current_setting('app.bypass_rls', true) = 'true'
                    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
                )
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id')::text)
                """
            )
        )
