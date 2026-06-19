"""add_join_code_to_tenants

Revision ID: b2c3d4e5f6a7
Revises: 41a9372f0044
Create Date: 2026-06-19 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import secrets
import string


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "41a9372f0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _generate_code() -> str:
    """Generate a unique 8-character alphanumeric join code."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.add_column(sa.Column("join_code", sa.String(length=20), nullable=True))

    # Backfill existing tenants with unique join codes
    from sqlalchemy import text

    conn = op.get_bind()
    result = conn.execute(text("SELECT id FROM tenants"))
    used = set()
    for row in result:
        while True:
            code = _generate_code()
            if code not in used:
                used.add(code)
                break
        conn.execute(
            text("UPDATE tenants SET join_code = :code WHERE id = :id"),
            {"code": code, "id": row[0]},
        )

    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.alter_column("join_code", nullable=False)
        batch_op.create_unique_constraint("uq_tenants_join_code", ["join_code"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_constraint("uq_tenants_join_code", type_="unique")
        batch_op.drop_column("join_code")
