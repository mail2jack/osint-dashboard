"""add ON DELETE CASCADE to case_subjects and subject_relations FKs

Revision ID: a1b2c3d4e5f7
Revises: d2e3f4a5b6c7
Create Date: 2026-08-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a1b2c3d4e5f7"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _count(conn, table: str) -> int:
    return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()


def _count_orphans_cs(conn) -> int:
    """case_subjects rows whose FK points to a non-existent parent."""
    return conn.execute(
        sa.text(
            "SELECT count(*) FROM case_subjects "
            "WHERE case_id NOT IN (SELECT id FROM cases) "
            "OR subject_id NOT IN (SELECT id FROM subjects)"
        )
    ).scalar()


def _count_orphans_sr(conn) -> int:
    return conn.execute(
        sa.text(
            "SELECT count(*) FROM subject_relations "
            "WHERE subject_id NOT IN (SELECT id FROM subjects) "
            "OR related_subject_id NOT IN (SELECT id FROM subjects)"
        )
    ).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    # ── Bypass RLS for orphan cleanup subqueries (PostgreSQL only) ──
    # cases/subjects have FORCE RLS — without bypass, SELECT id FROM cases
    # returns 0 rows, making NOT IN (empty) evaluate TRUE for ALL rows
    # and accidentally deleting every junction row.  (Incident 20 aug 2026.)
    if is_pg:
        conn.execute(sa.text("SET LOCAL app.bypass_rls = 'true'"))

    # ── Pre-counts: log what we have before cleanup ──
    pre_cs = _count(conn, "case_subjects")
    pre_sr = _count(conn, "subject_relations")
    orphan_cs = _count_orphans_cs(conn)
    orphan_sr = _count_orphans_sr(conn)
    print(
        f"  [cascade-fk] pre-count: case_subjects={pre_cs} (orphans={orphan_cs}), "
        f"subject_relations={pre_sr} (orphans={orphan_sr})"
    )

    # ── Sanity guard: without RLS bypass on PG, the subquery returns 0 rows
    # and ALL rows would be deleted.  Abort immediately if orphans == total
    # (meaning the subquery returned nothing — RLS bypass is missing). ──
    if is_pg and pre_cs > 0 and orphan_cs == pre_cs:
        raise RuntimeError(
            "ABORT: orphan cleanup subquery returned 0 cases — "
            "FORCE RLS is filtering all rows.  Ensure SET LOCAL "
            "app.bypass_rls = 'true' is active before cleanup."
        )

    # ── Clean up orphan rows that violate FK integrity ──
    conn.execute(
        sa.text(
            "DELETE FROM case_subjects "
            "WHERE case_id NOT IN (SELECT id FROM cases) "
            "OR subject_id NOT IN (SELECT id FROM subjects)"
        )
    )
    conn.execute(
        sa.text(
            "DELETE FROM subject_relations "
            "WHERE subject_id NOT IN (SELECT id FROM subjects) "
            "OR related_subject_id NOT IN (SELECT id FROM subjects)"
        )
    )

    # ── Post-counts: abort if we deleted more than just orphans ──
    post_cs = _count(conn, "case_subjects")
    post_sr = _count(conn, "subject_relations")
    deleted_cs = pre_cs - post_cs
    deleted_sr = pre_sr - post_sr
    print(
        f"  [cascade-fk] post-count: case_subjects={post_cs} (deleted {deleted_cs}), "
        f"subject_relations={post_sr} (deleted {deleted_sr})"
    )

    if deleted_cs > orphan_cs:
        raise RuntimeError(
            f"ABORT: deleted {deleted_cs} case_subjects but only {orphan_cs} "
            "were orphans — valid rows were removed!"
        )
    if deleted_sr > orphan_sr:
        raise RuntimeError(
            f"ABORT: deleted {deleted_sr} subject_relations but only {orphan_sr} "
            "were orphans — valid rows were removed!"
        )

    # ── case_subjects: drop old FKs and re-create with ON DELETE CASCADE ──
    # On PostgreSQL, explicitly drop and re-create to get CASCADE.
    # On SQLite, batch mode recreates the whole table — only create_foreign_key needed.
    if is_pg:
        with op.batch_alter_table("case_subjects") as batch_op:
            for col in ("case_id", "subject_id"):
                old_name = f"case_subjects_{col}_fkey"
                try:
                    batch_op.drop_constraint(old_name, type_="foreignkey")
                except Exception:
                    pass

            batch_op.create_foreign_key(
                "case_subjects_case_id_fkey",
                "cases",
                ["case_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_foreign_key(
                "case_subjects_subject_id_fkey",
                "subjects",
                ["subject_id"],
                ["id"],
                ondelete="CASCADE",
            )

        # ── subject_relations: drop old FKs and re-create with ON DELETE CASCADE ──
        with op.batch_alter_table("subject_relations") as batch_op:
            for col in ("subject_id", "related_subject_id"):
                old_name = f"subject_relations_{col}_fkey"
                try:
                    batch_op.drop_constraint(old_name, type_="foreignkey")
                except Exception:
                    pass

            batch_op.create_foreign_key(
                "subject_relations_subject_id_fkey",
                "subjects",
                ["subject_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_foreign_key(
                "subject_relations_related_subject_id_fkey",
                "subjects",
                ["related_subject_id"],
                ["id"],
                ondelete="CASCADE",
            )
    else:
        # SQLite: no-op — SQLite has no ALTER TABLE for constraints,
        # and batch recreate handles it. FKs are advisory in SQLite anyway.
        pass


def downgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    if is_pg:
        # Re-create FKs without CASCADE (original behavior)
        with op.batch_alter_table("case_subjects") as batch_op:
            for name in (
                "case_subjects_case_id_fkey",
                "case_subjects_subject_id_fkey",
            ):
                try:
                    batch_op.drop_constraint(name, type_="foreignkey")
                except Exception:
                    pass

            batch_op.create_foreign_key(
                "case_subjects_case_id_fkey",
                "cases",
                ["case_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                "case_subjects_subject_id_fkey",
                "subjects",
                ["subject_id"],
                ["id"],
            )

        with op.batch_alter_table("subject_relations") as batch_op:
            for name in (
                "subject_relations_subject_id_fkey",
                "subject_relations_related_subject_id_fkey",
            ):
                try:
                    batch_op.drop_constraint(name, type_="foreignkey")
                except Exception:
                    pass

            batch_op.create_foreign_key(
                "subject_relations_subject_id_fkey",
                "subjects",
                ["subject_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                "subject_relations_related_subject_id_fkey",
                "subjects",
                ["related_subject_id"],
                ["id"],
            )
