"""Add optional provenance and corroboration metadata.

All columns are additive and nullable/defaulted so historical findings and
research actions remain compatible with the existing workflow and reports.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fp1a2b3c4d5e"
down_revision: str | None = "fo1f2a3b4c5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("observation", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("interpretation", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("source_origin_type", sa.String(length=30), nullable=True))
    op.add_column("findings", sa.Column("source_published_at", sa.DateTime(), nullable=True))
    op.add_column("findings", sa.Column("source_observed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "findings",
        sa.Column("corroboration_status", sa.String(length=30), nullable=False, server_default="unassessed"),
    )
    op.add_column(
        "findings",
        sa.Column("corroboration_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("findings", sa.Column("confidence_reasons", sa.JSON(), nullable=True))

    op.add_column("research_actions", sa.Column("source_url", sa.String(length=2000), nullable=True))
    op.add_column("research_actions", sa.Column("method", sa.String(length=100), nullable=True))
    op.add_column("research_actions", sa.Column("source_origin_type", sa.String(length=30), nullable=True))
    op.add_column("research_actions", sa.Column("observed_at", sa.DateTime(), nullable=True))
    op.add_column("research_actions", sa.Column("operator_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    for column in (
        "operator_notes",
        "observed_at",
        "source_origin_type",
        "method",
        "source_url",
    ):
        op.drop_column("research_actions", column)
    for column in (
        "confidence_reasons",
        "corroboration_count",
        "corroboration_status",
        "source_observed_at",
        "source_published_at",
        "source_origin_type",
        "interpretation",
        "observation",
    ):
        op.drop_column("findings", column)
