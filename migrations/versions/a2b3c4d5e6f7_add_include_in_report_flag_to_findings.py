"""add include_in_report flag to findings

Revision ID: a2b3c4d5e6f7
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 09:00:00.000000

Report section (ADR-0001, optie (b)) — generieke vlag op findings:

* ``NULL``  → mee te nemen in officiële rapporten (backward compatible)
* ``true``  → mee te nemen in officiële rapporten
* ``false`` → te *excluderen* uit officiële rapporten

De filter ``(include_in_report IS NULL OR include_in_report = true)`` wordt
toegepast op alle officiële rapportroutes (workflow PV, case report HTML+PDF,
template-report). Ruwe exports/search/analytics blijven ongefilterd tellen.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
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
    if not _has_column("findings", "include_in_report"):
        with op.batch_alter_table("findings", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("include_in_report", sa.Boolean(), nullable=True)
            )


def downgrade() -> None:
    if _has_column("findings", "include_in_report"):
        with op.batch_alter_table("findings", schema=None) as batch_op:
            batch_op.drop_column("include_in_report")
