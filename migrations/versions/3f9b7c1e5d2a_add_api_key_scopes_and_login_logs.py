"""add api_key scopes and login_logs table

Revision ID: 3f9b7c1e5d2a
Revises: 343e157d02a4
Create Date: 2026-05-30 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import cms.models  # noqa: F401 — provides SafeJSON type


# revision identifiers, used by Alembic.
revision: str = "3f9b7c1e5d2a"
down_revision: Union[str, Sequence[str], None] = "343e157d02a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("api_keys")]

    if "scopes" not in columns:
        with op.batch_alter_table("api_keys", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("scopes", cms.models.SafeJSON(), nullable=True)
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_column("scopes")
