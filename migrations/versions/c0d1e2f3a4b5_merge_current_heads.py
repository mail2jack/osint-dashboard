"""Merge the parallel migration heads.

Revision ID: c0d1e2f3a4b5
Revises: b8c9d0e1f4a5, f4a5b6c7d8e0
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = (
    "b8c9d0e1f4a5",
    "f4a5b6c7d8e0",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge the heads without applying additional schema changes."""


def downgrade() -> None:
    """Split the merge point back into its two parent heads."""
