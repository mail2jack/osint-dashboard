"""add created_by to clients and subjects

Revision ID: 29bb9c967909
Revises: c1798b970286

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "29bb9c967909"
down_revision: Union[str, Sequence[str], None] = "c1798b970286"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    if bind.engine.dialect.name == "postgresql":
        result = bind.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return result is not None
    else:
        result = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
        return any(row[1] == column for row in result)


def upgrade() -> None:
    if not _has_column("clients", "created_by"):
        with op.batch_alter_table("clients", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("created_by", sa.String(length=36), nullable=True)
            )
            batch_op.create_index(
                batch_op.f("ix_clients_created_by"), ["created_by"], unique=False
            )
            batch_op.create_foreign_key(
                "fk_clients_created_by", "users", ["created_by"], ["id"]
            )

    if not _has_column("subjects", "created_by"):
        with op.batch_alter_table("subjects", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("created_by", sa.String(length=36), nullable=True)
            )
            batch_op.create_index(
                batch_op.f("ix_subjects_created_by"), ["created_by"], unique=False
            )
            batch_op.create_foreign_key(
                "fk_subjects_created_by", "users", ["created_by"], ["id"]
            )


def downgrade() -> None:
    if _has_column("subjects", "created_by"):
        with op.batch_alter_table("subjects", schema=None) as batch_op:
            batch_op.drop_constraint("fk_subjects_created_by", type_="foreignkey")
            batch_op.drop_index(batch_op.f("ix_subjects_created_by"))
            batch_op.drop_column("created_by")

    if _has_column("clients", "created_by"):
        with op.batch_alter_table("clients", schema=None) as batch_op:
            batch_op.drop_constraint("fk_clients_created_by", type_="foreignkey")
            batch_op.drop_index(batch_op.f("ix_clients_created_by"))
            batch_op.drop_column("created_by")
