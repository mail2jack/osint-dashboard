"""re-enable FORCE RLS with proper auth flow support

After e0f1's FORCE RLS was temporarily reverted to unblock anonymous auth
flows (login, signup, 2FA, invite-accept), the application code now sets
tenant context before every RLS-protected write in those flows. This
migration re-applies FORCE ROW LEVEL SECURITY with WITH CHECK policies
so that INSERT/UPDATE without a valid tenant context are rejected at the
database level.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "b1f2e3d4c5a6"
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
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id')::text)
                """
            )
        )
