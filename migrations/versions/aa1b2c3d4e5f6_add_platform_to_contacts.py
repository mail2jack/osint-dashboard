"""add platform to contacts for social media contacts

Revision ID: aa1b2c3d4e5f6
Revises: a2b3c4d5e6f7
Create Date: 2026-08-28 10:00:00.000000

Social media accounts as a contact type (``contact_type = 'social'``):

* ``value``    → the account name / handle (encrypted)
* ``platform`` → the social media platform (facebook, twitter/x, instagram, ...)
                 stored in plaintext so it can be listed/searched by label.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "aa1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_context().bind
    inspector = inspect(bind)
    try:
        columns = [c["name"] for c in inspector.get_columns(table)]
    except Exception:
        return False
    return column in columns


def upgrade() -> None:
    if not _has_column("contacts", "platform"):
        with op.batch_alter_table("contacts", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("platform", sa.String(length=50), nullable=True)
            )


def downgrade() -> None:
    if _has_column("contacts", "platform"):
        with op.batch_alter_table("contacts", schema=None) as batch_op:
            batch_op.drop_column("platform")
