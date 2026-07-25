"""add photo_metadata to subjects

Revision ID: b3c4d5e6f7a8
Revises: f7a8b9c0d1e2
Create Date: 2026-07-25 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from cms.models import SafeJSON


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subjects", sa.Column("photo_metadata", SafeJSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("subjects", "photo_metadata")
