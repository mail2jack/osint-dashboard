"""add follow-up fields for other customer questions"""
from alembic import op
import sqlalchemy as sa

revision = "fi9d0e1f2a34"
down_revision = "fh8c9d0e1f23"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customer_registration_requests", sa.Column("staff_response", sa.Text(), nullable=True))
    op.add_column("customer_registration_requests", sa.Column("internal_note", sa.Text(), nullable=True))
    op.add_column("customer_registration_requests", sa.Column("follow_up_at", sa.DateTime(), nullable=True))
    op.add_column("customer_registration_requests", sa.Column("handled_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("customer_registration_requests", "handled_at")
    op.drop_column("customer_registration_requests", "follow_up_at")
    op.drop_column("customer_registration_requests", "internal_note")
    op.drop_column("customer_registration_requests", "staff_response")
