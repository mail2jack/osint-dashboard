"""Add indexes declared by the current models but absent from staging.

Revision ID: a7b8c9d0e1f3
Revises: f4a5b6c7d8e9

This migration is deliberately additive.  Historical indexes that Alembic
reports as removed are retained; they are not safe to drop automatically.
"""

from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f3"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


INDEXES = (
    ("ix_addresses_action_id", "addresses", ("action_id",)),
    ("ix_addresses_finding_id", "addresses", ("finding_id",)),
    ("ix_contacts_action_id", "contacts", ("action_id",)),
    ("ix_contacts_finding_id", "contacts", ("finding_id",)),
    ("ix_investigations_archived_at", "investigations", ("archived_at",)),
    ("ix_social_accounts_action_id", "social_accounts", ("action_id",)),
    ("ix_subject_facts_action_id", "subject_facts", ("action_id",)),
    ("ix_subject_facts_finding_id", "subject_facts", ("finding_id",)),
    ("ix_subject_identifiers_action_id", "subject_identifiers", ("action_id",)),
    ("ix_subject_identifiers_finding_id", "subject_identifiers", ("finding_id",)),
    ("ix_subject_identifiers_tenant_id", "subject_identifiers", ("tenant_id",)),
)


def _existing_indexes(connection):
    inspector = sa.inspect(connection)
    return {
        (table, index["name"])
        for _, table, _ in INDEXES
        for index in inspector.get_indexes(table)
    }


def upgrade():
    connection = op.get_bind()
    existing = _existing_indexes(connection)
    for name, table, columns in INDEXES:
        if (table, name) not in existing:
            op.create_index(name, table, list(columns), unique=False)


def downgrade():
    connection = op.get_bind()
    existing = _existing_indexes(connection)
    for name, table, _ in reversed(INDEXES):
        if (table, name) in existing:
            op.drop_index(name, table_name=table)
