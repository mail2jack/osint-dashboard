"""add ON DELETE CASCADE to case_subjects and subject_relations FKs

Revision ID: a1b2c3d4e5f7
Revises: d2e3f4a5b6c7
Create Date: 2026-08-20 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


revision: str = "a1b2c3d4e5f7"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_fk_constraints(table: str, constraint_name: str) -> list[str]:
    """Find FK constraint names matching a pattern on a table."""
    bind = op.get_context().bind
    inspector = inspect(bind)
    fks = inspector.get_foreign_keys(table)
    return [fk["name"] for fk in fks if fk["name"] and constraint_name in fk["name"]]


def upgrade() -> None:
    # case_subjects: drop old FKs and re-create with ON DELETE CASCADE
    for col in ("case_id", "subject_id"):
        old_name = f"case_subjects_{col}_fkey"
        try:
            op.drop_constraint(old_name, "case_subjects", type_="foreignkey")
        except Exception:
            pass  # constraint may have a different name

    op.create_foreign_key(
        "case_subjects_case_id_fkey",
        "case_subjects",
        "cases",
        ["case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "case_subjects_subject_id_fkey",
        "case_subjects",
        "subjects",
        ["subject_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # subject_relations: drop old FKs and re-create with ON DELETE CASCADE
    for col in ("subject_id", "related_subject_id"):
        old_name = f"subject_relations_{col}_fkey"
        try:
            op.drop_constraint(old_name, "subject_relations", type_="foreignkey")
        except Exception:
            pass

    op.create_foreign_key(
        "subject_relations_subject_id_fkey",
        "subject_relations",
        "subjects",
        ["subject_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "subject_relations_related_subject_id_fkey",
        "subject_relations",
        "subjects",
        ["related_subject_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Re-create FKs without CASCADE (original behavior)
    for name in (
        "case_subjects_case_id_fkey",
        "case_subjects_subject_id_fkey",
    ):
        try:
            op.drop_constraint(name, "case_subjects", type_="foreignkey")
        except Exception:
            pass

    op.create_foreign_key(
        "case_subjects_case_id_fkey",
        "case_subjects",
        "cases",
        ["case_id"],
        ["id"],
    )
    op.create_foreign_key(
        "case_subjects_subject_id_fkey",
        "case_subjects",
        "subjects",
        ["subject_id"],
        ["id"],
    )

    for name in (
        "subject_relations_subject_id_fkey",
        "subject_relations_related_subject_id_fkey",
    ):
        try:
            op.drop_constraint(name, "subject_relations", type_="foreignkey")
        except Exception:
            pass

    op.create_foreign_key(
        "subject_relations_subject_id_fkey",
        "subject_relations",
        "subjects",
        ["subject_id"],
        ["id"],
    )
    op.create_foreign_key(
        "subject_relations_related_subject_id_fkey",
        "subject_relations",
        "subjects",
        ["related_subject_id"],
        ["id"],
    )
