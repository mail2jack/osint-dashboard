"""add missing indexes across all tables

Revision ID: a1b2c3d4e5f6
Revises: 3f9b7c1e5d2a
Create Date: 2026-06-01 17:55:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "3f9b7c1e5d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(table: str, index_name: str) -> bool:
    """Check if an index exists (cross-dialect)."""
    bind = op.get_context().bind
    inspector = inspect(bind)
    indexes = [i["name"] for i in inspector.get_indexes(table)]
    return index_name in indexes


def _add_index_if_missing(
    table: str, columns: list, index_name: str | None = None
) -> None:
    """Add an index only if it doesn't already exist."""
    idx_name = index_name or f"ix_{table}_{'_'.join(columns)}"
    if not _has_index(table, idx_name):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_index(idx_name, columns, unique=False)


def upgrade() -> None:
    """Add missing indexes."""
    # clients
    _add_index_if_missing("clients", ["is_deleted"])

    # subjects
    _add_index_if_missing("subjects", ["subject_type"])
    _add_index_if_missing("subjects", ["is_deleted"])

    # cases — FK and boolean indexes
    _add_index_if_missing("cases", ["created_by"])
    _add_index_if_missing("cases", ["assigned_to"])
    _add_index_if_missing("cases", ["lead_investigator_id"])
    _add_index_if_missing("cases", ["reopened_by"])
    _add_index_if_missing("cases", ["parent_case_id"])
    _add_index_if_missing("cases", ["is_deleted"])

    # financial_records
    _add_index_if_missing("financial_records", ["case_id"])
    _add_index_if_missing("financial_records", ["subject_id"])
    _add_index_if_missing("financial_records", ["verified_by"])
    _add_index_if_missing("financial_records", ["is_deleted"])

    # findings
    _add_index_if_missing("findings", ["finding_type"])
    _add_index_if_missing("findings", ["created_by"])
    _add_index_if_missing("findings", ["is_deleted"])

    # screenshots
    _add_index_if_missing("screenshots", ["case_id"])
    _add_index_if_missing("screenshots", ["created_by"])

    # audit_logs
    _add_index_if_missing("audit_logs", ["user_id"])

    # documents
    _add_index_if_missing("documents", ["case_id"])
    _add_index_if_missing("documents", ["subject_id"])
    _add_index_if_missing("documents", ["financial_record_id"])
    _add_index_if_missing("documents", ["uploaded_by"])
    _add_index_if_missing("documents", ["is_deleted"])

    # comments
    _add_index_if_missing("comments", ["case_id"])
    _add_index_if_missing("comments", ["subject_id"])
    _add_index_if_missing("comments", ["client_id"])
    _add_index_if_missing("comments", ["financial_record_id"])
    _add_index_if_missing("comments", ["author_id"])
    _add_index_if_missing("comments", ["last_edited_by_id"])
    _add_index_if_missing("comments", ["is_deleted"])

    # comment_edit_history
    _add_index_if_missing("comment_edit_history", ["comment_id"])
    _add_index_if_missing("comment_edit_history", ["edited_by_id"])

    # document_templates
    _add_index_if_missing("document_templates", ["created_by"])

    # reminders
    _add_index_if_missing("reminders", ["case_id"])
    _add_index_if_missing("reminders", ["subject_id"])
    _add_index_if_missing("reminders", ["client_id"])
    _add_index_if_missing("reminders", ["assigned_to"])
    _add_index_if_missing("reminders", ["created_by"])
    _add_index_if_missing("reminders", ["is_deleted"])

    # settings
    _add_index_if_missing("settings", ["created_by"])

    # spiderfoot_scans
    _add_index_if_missing("spiderfoot_scans", ["case_id"])
    _add_index_if_missing("spiderfoot_scans", ["subject_id"])
    _add_index_if_missing("spiderfoot_scans", ["status"])
    _add_index_if_missing("spiderfoot_scans", ["created_by"])
    _add_index_if_missing("spiderfoot_scans", ["is_deleted"])

    # osint_searches
    _add_index_if_missing("osint_searches", ["case_id"])
    _add_index_if_missing("osint_searches", ["subject_id"])
    _add_index_if_missing("osint_searches", ["started_by"])

    # api_keys
    _add_index_if_missing("api_keys", ["user_id"])
    _add_index_if_missing("api_keys", ["is_active"])

    # phone_lookups
    _add_index_if_missing("phone_lookups", ["created_by"])


def downgrade() -> None:
    """Remove added indexes."""
    pass  # no downgrade needed for index additions
