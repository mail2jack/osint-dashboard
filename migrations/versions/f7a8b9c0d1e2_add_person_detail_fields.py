"""add person detail fields to subjects and clients tables

Revision ID: d4e5f6a7b8c9
Revises: a9b8c7d6e5f4
Create Date: 2026-07-23 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Subject table: new person fields
    with op.batch_alter_table("subjects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("achternaam", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("voornamen", sa.String(length=300), nullable=True)
        )
        batch_op.add_column(
            sa.Column("voorletters", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("tussenvoegsels", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(sa.Column("geslacht", sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column("bsn_number", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reisdocument_type", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reisdocument_nummer", sa.String(length=500), nullable=True)
        )

    # Client table: new person fields
    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("achternaam", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("voornamen", sa.String(length=300), nullable=True)
        )
        batch_op.add_column(
            sa.Column("voorletters", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("tussenvoegsels", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(sa.Column("geslacht", sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column("nationality", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("bsn_number", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("identification_number", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reisdocument_type", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reisdocument_nummer", sa.String(length=500), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.drop_column("reisdocument_nummer")
        batch_op.drop_column("reisdocument_type")
        batch_op.drop_column("identification_number")
        batch_op.drop_column("bsn_number")
        batch_op.drop_column("nationality")
        batch_op.drop_column("geslacht")
        batch_op.drop_column("tussenvoegsels")
        batch_op.drop_column("voorletters")
        batch_op.drop_column("voornamen")
        batch_op.drop_column("achternaam")

    with op.batch_alter_table("subjects", schema=None) as batch_op:
        batch_op.drop_column("reisdocument_nummer")
        batch_op.drop_column("reisdocument_type")
        batch_op.drop_column("bsn_number")
        batch_op.drop_column("geslacht")
        batch_op.drop_column("tussenvoegsels")
        batch_op.drop_column("voorletters")
        batch_op.drop_column("voornamen")
        batch_op.drop_column("achternaam")
