"""add background_tasks table

Revision ID: 8255fef205fa
Revises: 69999cbb5609
Create Date: 2026-05-29 17:07:10.994743

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8255fef205fa"
down_revision: Union[str, Sequence[str], None] = "69999cbb5609"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "background_tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("task_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("background_tasks", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_background_tasks_status"), ["status"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("background_tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_background_tasks_status"))
    op.drop_table("background_tasks")
