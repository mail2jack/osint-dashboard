"""Merge the AI research question migration with the main migration line."""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = (
    "c0d1e2f3a4b5",
    "f9b0c1d2e3f4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge the two existing migration lines without schema changes."""


def downgrade() -> None:
    """Split the merge point back into its two parent revisions."""
