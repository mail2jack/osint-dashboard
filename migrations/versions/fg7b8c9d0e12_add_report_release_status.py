"""add explicit customer report release status

Revision ID: fg7b8c9d0e12
Revises: ff6a7b8c9d01
"""

from alembic import op
import sqlalchemy as sa


revision = "fg7b8c9d0e12"
down_revision = "ff6a7b8c9d01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cases", sa.Column("report_status", sa.String(length=20), nullable=True))
    op.add_column("cases", sa.Column("report_released_at", sa.DateTime(), nullable=True))
    op.add_column("cases", sa.Column("report_released_by", sa.String(length=36), nullable=True))
    op.create_index("ix_cases_report_status", "cases", ["report_status"])
    op.create_foreign_key(
        "fk_cases_report_released_by", "cases", "users", ["report_released_by"], ["id"]
    )
    op.execute("UPDATE cases SET report_status = CASE WHEN pv_body IS NULL OR pv_body = '' THEN 'draft' ELSE 'released' END")
    op.alter_column("cases", "report_status", nullable=False, server_default="draft")


def downgrade():
    op.drop_constraint("fk_cases_report_released_by", "cases", type_="foreignkey")
    op.drop_index("ix_cases_report_status", table_name="cases")
    op.drop_column("cases", "report_released_by")
    op.drop_column("cases", "report_released_at")
    op.drop_column("cases", "report_status")
