"""subject-first data model phase 1 (ADR-0001 PR3)

Creates `subject_identifiers` and `subject_facts`, extends
addresses/contacts/social_accounts with source/status/timestamps/action
links, converts `subject_relations` to typed/directed single-row storage,
and adds role/status/note to `case_subjects`.

Revision ID: f0a1b2c3d4e5
Revises: e0f1a2b3c4d5
Create Date: 2026-08-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ["subject_identifiers", "subject_facts"]

_FAMILY_TYPES = (
    "family",
    "family_member",
    "relative",
    "parent",
    "child",
    "spouse",
    "partner",
    "sibling",
    "cousin",
    "in_law",
    "stepfamily",
    "grandparent",
    "grandchild",
    "uncle",
    "aunt",
    "nephew",
    "niece",
)
_BUSINESS_TYPES = (
    "business",
    "business_partner",
    "businesspartner",
    "colleague",
    "coworker",
    "work",
    "employer",
    "employee",
    "company",
    "co_owner",
    "client",
    "supplier",
    "accountant",
)


def _has_column(table: str, column: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def _add_provenance_columns(table: str) -> None:
    """Add the shared source/status/observed_at/action/finding/updated_by set."""
    with op.batch_alter_table(table) as batch_op:
        if not _has_column(table, "source"):
            batch_op.add_column(sa.Column("source", sa.String(200), nullable=True))
        if not _has_column(table, "status"):
            batch_op.add_column(
                sa.Column(
                    "status",
                    sa.String(20),
                    nullable=True,
                    server_default="candidate",
                )
            )
        if not _has_column(table, "observed_at"):
            batch_op.add_column(sa.Column("observed_at", sa.DateTime(), nullable=True))
        if not _has_column(table, "action_id"):
            batch_op.add_column(sa.Column("action_id", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_action_id", "research_actions", ["action_id"], ["id"]
            )
        if not _has_column(table, "finding_id"):
            batch_op.add_column(sa.Column("finding_id", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_finding_id", "findings", ["finding_id"], ["id"]
            )
        if not _has_column(table, "updated_by"):
            batch_op.add_column(sa.Column("updated_by", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_updated_by", "users", ["updated_by"], ["id"]
            )


def _in_literal(values: Sequence[str]) -> str:
    """Render trusted constant string tuples for a portable IN (...) clause."""
    return ", ".join(f"'{v}'" for v in values)


def _enable_rls() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
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


def upgrade() -> None:
    op.create_table(
        "subject_identifiers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "subject_id",
            sa.String(36),
            sa.ForeignKey("subjects.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("identifier_type", sa.String(50), nullable=False),
        sa.Column("value_enc", sa.Text(), nullable=True),
        sa.Column("fingerprint_keyed", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="candidate",
        ),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("reliability", sa.String(20), nullable=True),
        sa.Column(
            "action_id",
            sa.String(36),
            sa.ForeignKey("research_actions.id"),
            nullable=True,
        ),
        sa.Column(
            "finding_id",
            sa.String(36),
            sa.ForeignKey("findings.id"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Index("ix_subject_identifiers_subject_id", "subject_id"),
        sa.Index("ix_subject_identifiers_fingerprint_keyed", "fingerprint_keyed"),
        sa.Index(
            "ix_subject_identifiers_tenant_fingerprint",
            "tenant_id",
            "fingerprint_keyed",
        ),
    )

    op.create_table(
        "subject_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "subject_id",
            sa.String(36),
            sa.ForeignKey("subjects.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("fact_key", sa.String(100), nullable=False),
        sa.Column("value_enc", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="candidate",
        ),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("reliability", sa.String(20), nullable=True),
        sa.Column(
            "verified_by",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "action_id",
            sa.String(36),
            sa.ForeignKey("research_actions.id"),
            nullable=True,
        ),
        sa.Column(
            "finding_id",
            sa.String(36),
            sa.ForeignKey("findings.id"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Index("ix_subject_facts_subject_id", "subject_id"),
        sa.Index("ix_subject_facts_tenant_id", "tenant_id"),
        sa.Index("ix_subject_facts_subject_key", "subject_id", "fact_key"),
    )

    for table in ("addresses", "contacts", "social_accounts"):
        _add_provenance_columns(table)

    # subject_relations: rename + add direction/provenance columns
    with op.batch_alter_table("subject_relations") as batch_op:
        if not _has_column("subject_relations", "relation_type"):
            batch_op.alter_column("relationship_type", new_column_name="relation_type")
        if not _has_column("subject_relations", "direction"):
            batch_op.add_column(
                sa.Column(
                    "direction",
                    sa.String(20),
                    nullable=False,
                    server_default="mutual",
                )
            )
        if not _has_column("subject_relations", "source"):
            batch_op.add_column(sa.Column("source", sa.String(200), nullable=True))
        if not _has_column("subject_relations", "reliability"):
            batch_op.add_column(sa.Column("reliability", sa.String(20), nullable=True))
        if not _has_column("subject_relations", "status"):
            batch_op.add_column(
                sa.Column(
                    "status",
                    sa.String(20),
                    nullable=True,
                    server_default="candidate",
                )
            )
        if not _has_column("subject_relations", "observed_at"):
            batch_op.add_column(sa.Column("observed_at", sa.DateTime(), nullable=True))
        if not _has_column("subject_relations", "case_number"):
            batch_op.add_column(sa.Column("case_number", sa.String(50), nullable=True))
        if not _has_column("subject_relations", "created_by"):
            batch_op.add_column(sa.Column("created_by", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                "fk_subject_relations_created_by", "users", ["created_by"], ["id"]
            )

    bind = op.get_bind()
    # Normalize free-text relationship types into the ADR vocabulary
    bind.execute(
        sa.text(
            f"""
            UPDATE subject_relations
            SET relation_type = CASE
                WHEN lower(trim(relation_type)) IN ({_in_literal(_FAMILY_TYPES)})
                    THEN 'family'
                WHEN lower(trim(relation_type)) IN ({_in_literal(_BUSINESS_TYPES)})
                    THEN 'business'
                ELSE 'other'
            END
            WHERE relation_type IS NOT NULL
            """
        )
    )
    # Collapse legacy double-row storage into a single canonical row per pair
    # (canonical order = subject_id < related_subject_id, direction = mutual).
    bind.execute(
        sa.text(
            """
            DELETE FROM subject_relations
            WHERE subject_id > related_subject_id
              AND EXISTS (
                SELECT 1 FROM subject_relations r
                WHERE r.subject_id = subject_relations.related_subject_id
                  AND r.related_subject_id = subject_relations.subject_id
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE subject_relations
            SET subject_id = related_subject_id,
                related_subject_id = subject_id
            WHERE subject_id > related_subject_id
            """
        )
    )

    # case_subjects: role/status/note
    with op.batch_alter_table("case_subjects") as batch_op:
        if not _has_column("case_subjects", "role_in_case"):
            batch_op.add_column(sa.Column("role_in_case", sa.String(50), nullable=True))
        if not _has_column("case_subjects", "status"):
            batch_op.add_column(
                sa.Column(
                    "status",
                    sa.String(20),
                    nullable=False,
                    server_default="active",
                )
            )
        if not _has_column("case_subjects", "note"):
            batch_op.add_column(sa.Column("note", sa.Text(), nullable=True))

    _enable_rls()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in RLS_TABLES:
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            bind.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    with op.batch_alter_table("case_subjects") as batch_op:
        for column in ("role_in_case", "status", "note"):
            if _has_column("case_subjects", column):
                batch_op.drop_column(column)

    # Restore double-row storage before renaming the type column back
    bind.execute(
        sa.text(
            """
            INSERT INTO subject_relations
                (subject_id, related_subject_id, relation_type, created_at)
            SELECT related_subject_id, subject_id, relation_type, created_at
            FROM subject_relations
            WHERE direction = 'mutual'
            """
        )
    )
    with op.batch_alter_table("subject_relations") as batch_op:
        if _has_column("subject_relations", "relation_type"):
            batch_op.alter_column("relation_type", new_column_name="relationship_type")
        for column in (
            "direction",
            "source",
            "reliability",
            "status",
            "observed_at",
            "case_number",
            "created_by",
        ):
            if _has_column("subject_relations", column):
                batch_op.drop_column(column)

    for table in ("addresses", "contacts", "social_accounts"):
        with op.batch_alter_table(table) as batch_op:
            for column in (
                "source",
                "status",
                "observed_at",
                "action_id",
                "updated_by",
            ):
                if _has_column(table, column):
                    batch_op.drop_column(column)
            # finding_id pre-exists on social_accounts (migration c1d2e3f4a5b6),
            # so only drop it from addresses/contacts where PR3 added it.
            if table in ("addresses", "contacts") and _has_column(table, "finding_id"):
                batch_op.drop_column("finding_id")

    op.drop_table("subject_facts")
    op.drop_table("subject_identifiers")
