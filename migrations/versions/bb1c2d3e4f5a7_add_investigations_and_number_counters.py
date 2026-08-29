"""ADR-0002 PR2: investigations model + atomic tenant-scoped number counters

Creates the ``investigations`` table plus the two atomic counter tables
(``case_number_counters`` per tenant+year, ``investigation_seq_counters`` per
tenant+case) used for safe number issuance — never ``MAX()+1``.

Enforces the hard tenant invariant ``investigation.tenant_id ==
investigation.case.tenant_id`` with composite foreign keys referencing the
new ``uq_cases_id_tenant`` parenthesis key on ``cases``, works on PostgreSQL
and SQLite (no renumbering, no backfill of investigation records — only the
counter tables are initialized from existing matching ``??`` case numbers so
the first new allocation does not collide).

Revision ID: bb1c2d3e4f5a7
Revises: aa1b2c3d4e5f6
Create Date: 2026-08-29

"""
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "bb1c2d3e4f5a7"
down_revision: str | None = "aa1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = [
    "investigations",
    "case_number_counters",
    "investigation_seq_counters",
]


def _enable_rls_and_context() -> None:
    """Enable FORCE RLS with tenant_isolation policies (PostgreSQL only).

    Runs under an explicit transaction-local ``app.bypass_rls`` context so
    the counter seed below can read ``cases`` and write the counters even
    though the new tables are FORCE-protected.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text("SELECT set_config('app.bypass_rls', 'true', true)")
    )
    for table in RLS_TABLES:
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


def _seed_case_number_counters() -> None:
    """Initialize case-number counters from existing ``YYYY-NNNNN`` cases.

    Required so the first allocation after upgrading a populated database
    continues above the highest already-issued number instead of colliding
    with ``uq_tenant_case_number``. Existing records are never modified or
    renumbered; non-standard numbers are ignored.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT tenant_id, case_number FROM cases")).fetchall()

    highest: dict[tuple[str, int], int] = {}
    for tenant_id, number in rows:
        num = number or ""
        if len(num) < 6 or num[4] != "-":
            continue
        prefix, suffix = num[:4], num[5:]
        if not (prefix.isdigit() and suffix.isdigit()):
            continue
        year = int(prefix)
        value = int(suffix)
        key = (tenant_id, year)
        if value > highest.get(key, 0):
            highest[key] = value

    counters = sa.table(
        "case_number_counters",
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


_COMPOSITE_CASE_FK = (
    "case_id",
    "tenant_id",
)


def upgrade() -> None:
    # Parent-key for the composite FKs below (ADR-0002 D8): must exist before
    # the child tables reference cases(id, tenant_id).
    with op.batch_alter_table("cases") as batch_op:
        batch_op.create_unique_constraint(
            "uq_cases_id_tenant", ["id", "tenant_id"]
        )

    op.create_table(
        "investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="open",
        ),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "sequence_no",
            name="uq_investigation_seq_per_case",
        ),
        sa.ForeignKeyConstraint(
            list(_COMPOSITE_CASE_FK),
            ["cases.id", "cases.tenant_id"],
            name="fk_investigations_case_tenant",
        ),
        sa.Index("ix_investigations_case_id", "case_id"),
    )

    op.create_table(
        "case_number_counters",
        sa.Column("tenant_id", sa.String(36), primary_key=True),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column(
            "next_seq",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "investigation_seq_counters",
        sa.Column("tenant_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), primary_key=True),
        sa.Column(
            "next_seq",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            list(_COMPOSITE_CASE_FK),
            ["cases.id", "cases.tenant_id"],
            name="fk_investigation_seq_counter_case_tenant",
        ),
    )

    _enable_rls_and_context()
    _seed_case_number_counters()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in RLS_TABLES:
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_table("investigation_seq_counters")
    op.drop_table("case_number_counters")
    op.drop_table("investigations")

    with op.batch_alter_table("cases") as batch_op:
        batch_op.drop_constraint("uq_cases_id_tenant", type_="unique")