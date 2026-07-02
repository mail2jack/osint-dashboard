"""workflow integration: new models + columns

Revision ID: 26519d16f4f2
Revises: 29bb9c967909
Create Date: 2026-06-25 13:41:26.000556

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from cms.models import SafeJSON

# revision identifiers, used by Alembic.
revision: str = "26519d16f4f2"
down_revision: Union[str, Sequence[str], None] = "29bb9c967909"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New tables
    op.create_table(
        "service_rates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("service_type", sa.String(50), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_rates_service_type", "service_rates", ["service_type"])
    op.create_index("ix_service_rates_tenant_id", "service_rates", ["tenant_id"])

    op.create_table(
        "research_actions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("data_value", sa.Text(), nullable=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_actions_case_id", "research_actions", ["case_id"])
    op.create_index("ix_research_actions_tenant_id", "research_actions", ["tenant_id"])

    op.create_table(
        "action_findings",
        sa.Column("action_id", sa.String(36), nullable=False),
        sa.Column("finding_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["research_actions.id"]),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"]),
        sa.PrimaryKeyConstraint("action_id", "finding_id"),
    )

    op.create_table(
        "finding_screenshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("finding_id", sa.String(36), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_finding_screenshots_finding_id",
        "finding_screenshots",
        ["finding_id"],
    )
    op.create_index(
        "ix_finding_screenshots_tenant_id",
        "finding_screenshots",
        ["tenant_id"],
    )

    # New columns on existing tables
    op.add_column("clients", sa.Column("reference", sa.String(100), nullable=True))
    op.add_column("cases", sa.Column("pv_body", sa.Text(), nullable=True))
    op.add_column("cases", sa.Column("pv_updated_at", sa.DateTime(), nullable=True))
    op.add_column("findings", sa.Column("detail", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("icon", sa.String(10), nullable=True))
    op.add_column("findings", sa.Column("verified", sa.Boolean(), nullable=True))
    op.add_column("findings", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("raw_data", SafeJSON(), nullable=True))
    op.add_column("findings", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.create_index("ix_findings_archived_at", "findings", ["archived_at"])
    op.add_column(
        "subjects", sa.Column("workflow_social_accounts", SafeJSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("subjects", "workflow_social_accounts")
    op.drop_index("ix_findings_archived_at", table_name="findings")
    op.drop_column("findings", "archived_at")
    op.drop_column("findings", "raw_data")
    op.drop_column("findings", "comment")
    op.drop_column("findings", "verified")
    op.drop_column("findings", "icon")
    op.drop_column("findings", "detail")
    op.drop_column("cases", "pv_updated_at")
    op.drop_column("cases", "pv_body")
    op.drop_column("clients", "reference")
    op.drop_index("ix_finding_screenshots_tenant_id", table_name="finding_screenshots")
    op.drop_index("ix_finding_screenshots_finding_id", table_name="finding_screenshots")
    op.drop_table("finding_screenshots")
    op.drop_table("action_findings")
    op.drop_index("ix_research_actions_tenant_id", table_name="research_actions")
    op.drop_index("ix_research_actions_case_id", table_name="research_actions")
    op.drop_table("research_actions")
    op.drop_index("ix_service_rates_tenant_id", table_name="service_rates")
    op.drop_index("ix_service_rates_service_type", table_name="service_rates")
    op.drop_table("service_rates")
