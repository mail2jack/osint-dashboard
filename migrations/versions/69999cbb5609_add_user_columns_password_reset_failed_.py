"""add user columns password_reset failed_login locked_until

Revision ID: 69999cbb5609
Revises: 8c4bb90d2490
Create Date: 2026-05-26 19:13:09.277621

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "69999cbb5609"
down_revision: str | Sequence[str] | None = "8c4bb90d2490"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    """Check if a column exists in the table (cross-dialect)."""
    bind = op.get_context().bind
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _has_column("users", "password_reset_token"):
        op.add_column(
            "users",
            sa.Column("password_reset_token", sa.String(length=128), nullable=True),
        )
    if not _has_column("users", "password_reset_expires"):
        op.add_column(
            "users", sa.Column("password_reset_expires", sa.DateTime(), nullable=True)
        )
    if not _has_column("users", "failed_login_attempts"):
        op.add_column(
            "users",
            sa.Column(
                "failed_login_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if not _has_column("users", "locked_until"):
        op.add_column("users", sa.Column("locked_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "locked_until"):
        op.drop_column("users", "locked_until")
    if _has_column("users", "failed_login_attempts"):
        op.drop_column("users", "failed_login_attempts")
    if _has_column("users", "password_reset_expires"):
        op.drop_column("users", "password_reset_expires")
    if _has_column("users", "password_reset_token"):
        op.drop_column("users", "password_reset_token")
