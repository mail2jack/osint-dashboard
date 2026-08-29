"""P1: per-tenant invoice numbering — atomic counter + composite unique

Replaces the *global* unique index on ``invoices.invoice_number`` with the
composite ``(tenant_id, invoice_number)`` constraint and adds an atomic
per-``(tenant_id, year)`` counter table (``invoice_number_counters``), so
invoice numbers are issued like case/investigation numbers — never
``MAX()+1``.

Background (P1 incident, 2026-08-29): ``Invoice.generate_invoice_number()``
read the "last" number with a tenant-RLS-scoped query while the unique index
was global. Two tenants therefore computed the same next number and the world
global index rejected the second one with a UniqueViolation, surfacing as a
500 on case-create *after* the case had already committed.

With this migration:

- two tenants may legitimately hold the same ``FAC-YYYY-NNNNN``;
- within one tenant numbers stay unique and sequential (counter + composite
  constraint);
- the counter is seeded from existing invoices per (tenant, year) so the
  first allocation after upgrading continues above the highest issued number.

Revision ID: a6b7c8d9e0f1
Revises: dd1e2f3a4b5c7
Create Date: 2026-08-29

"""
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "dd1e2f3a4b5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls_for_counter() -> None:
    """Enable FORCE RLS with the tenant_isolation policy (PostgreSQL only).

    Runs under a transaction-local ``app.bypass_rls`` context so the counter
    seed below can read ``invoices`` and write the counter rows even though
    the new table is FORCE-protected and ``invoices`` is too.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(sa.text("SELECT set_config('app.bypass_rls', 'true', true)"))
    bind.execute(
        sa.text(
            "ALTER TABLE invoice_number_counters ENABLE ROW LEVEL SECURITY"
        )
    )
    bind.execute(
        sa.text(
            "ALTER TABLE invoice_number_counters FORCE ROW LEVEL SECURITY"
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON invoice_number_counters
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


def _disable_rls_for_counter() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text("DROP POLICY IF EXISTS tenant_isolation ON invoice_number_counters")
    )
    bind.execute(
        sa.text("ALTER TABLE invoice_number_counters NO FORCE ROW LEVEL SECURITY")
    )
    bind.execute(
        sa.text("ALTER TABLE invoice_number_counters DISABLE ROW LEVEL SECURITY")
    )


def _seed_invoice_counters() -> None:
    """Initialize per-tenant invoice counters from existing ``FAC-YYYY-NNNNN``
    invoices.

    Required so the first allocation after upgrading a populated database
    continues above the highest already-issued number instead of colliding
    with ``uq_tenant_invoice_number``. Existing records are never modified or
    renumbered; non-standard numbers are ignored.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT tenant_id, invoice_number FROM invoices")
    ).fetchall()

    highest: dict[tuple[str, int], int] = {}
    for tenant_id, number in rows:
        num = number or ""
        parts = num.split("-")
        if len(parts) != 3 or parts[0] != "FAC":
            continue
        year_str, seq_str = parts[1], parts[2]
        if not (year_str.isdigit() and seq_str.isdigit()):
            continue
        key = (tenant_id, int(year_str))
        value = int(seq_str)
        if value > highest.get(key, 0):
            highest[key] = value

    counters = sa.table(
        "invoice_number_counters",
        sa.column("tenant_id", sa.String),
        sa.column("year", sa.Integer),
        sa.column("next_seq", sa.Integer),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime.now(timezone.utc)
    # next_seq stores the highest issued number; the next allocation
    # increments it (and returns +1) before handing it out.
    for (tenant_id, year), max_num in sorted(highest.items()):
        bind.execute(
            counters.insert().values(
                tenant_id=tenant_id,
                year=year,
                next_seq=max_num,
                updated_at=now,
            )
        )


def upgrade() -> None:
    op.create_table(
        "invoice_number_counters",
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id"),
            primary_key=True,
        ),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column(
            "next_seq",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "next_seq > 0", name="ck_invoice_number_counter_next_seq_positive"
        ),
    )

    _enable_rls_for_counter()
    _seed_invoice_counters()

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_invoices_invoice_number", table_name="invoices")
        op.create_unique_constraint(
            "uq_tenant_invoice_number",
            "invoices",
            ["tenant_id", "invoice_number"],
        )
    else:
        with op.batch_alter_table("invoices") as batch_op:
            batch_op.drop_index("ix_invoices_invoice_number")
            batch_op.create_unique_constraint(
                "uq_tenant_invoice_number", ["tenant_id", "invoice_number"]
            )


def _cross_tenant_invoice_duplicates(bind) -> list[tuple[str, int]]:
    """Invoice numbers that now legally appear in more than one tenant.

    Once such numbers exist the downgrade (which recreates the *global*
    unique index) is impossible without data work — so the downgrade must
    stop BEFORE any DDL instead of failing halfway or corrupting data.
    """
    rows = bind.execute(
        sa.text(
            "SELECT invoice_number, count(DISTINCT tenant_id) AS tenant_count "
            "FROM invoices "
            "WHERE invoice_number IS NOT NULL "
            "GROUP BY invoice_number "
            "HAVING count(DISTINCT tenant_id) > 1 "
            "ORDER BY invoice_number"
        )
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The guard reads across ALL tenants; FORCE RLS would otherwise hide
        # the very duplicates we must detect.
        bind.execute(sa.text("SELECT set_config('app.bypass_rls', 'true', true)"))

    duplicates = _cross_tenant_invoice_duplicates(bind)
    if duplicates:
        shown = ", ".join(f"{num} ({count} tenants)" for num, count in duplicates[:5])
        more = "" if len(duplicates) <= 5 else f" (en {len(duplicates) - 5} meer)"
        raise RuntimeError(
            "Downgrade van a6b7c8d9e0f1 is not safe: deze invoice_number komt "
            "in meerdere tenants voor, namelijk "
            f"{shown}{more}. Het opnieuw aanmaken van de globale unieke index "
            "ix_invoices_invoice_number zou falen. Veilig rollbackpad: "
            "fix-forward, of herstel vanaf de pre-deploy backup — voer GEEN "
            "impliciete alembic downgrade uit."
        )

    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_tenant_invoice_number", "invoices", type_="unique")
        op.create_index(
            "ix_invoices_invoice_number",
            "invoices",
            ["invoice_number"],
            unique=True,
        )
    else:
        with op.batch_alter_table("invoices") as batch_op:
            batch_op.drop_constraint("uq_tenant_invoice_number", type_="unique")
            batch_op.create_index(
                "ix_invoices_invoice_number", ["invoice_number"], unique=True
            )

    _disable_rls_for_counter()
    op.drop_table("invoice_number_counters")