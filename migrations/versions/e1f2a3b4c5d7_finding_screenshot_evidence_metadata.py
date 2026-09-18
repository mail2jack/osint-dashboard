"""Add nullable evidence metadata to workflow finding screenshots.

Legacy manual uploads remain valid without a backfill.  Future automated
captures record the byte size and bounded provenance beside the existing
tenant/finding linkage and source URL, so reporting and quota checks do not
need to trust a filesystem path.

Revision ID: e1f2a3b4c5d7
Revises: e0f1a2b3c4d6
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d7"
down_revision: str | None = "e0f1a2b3c4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("finding_screenshots") as batch_op:
        batch_op.add_column(sa.Column("file_size", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("capture_provenance", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("finding_screenshots") as batch_op:
        batch_op.drop_column("capture_provenance")
        batch_op.drop_column("file_size")
