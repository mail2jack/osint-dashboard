"""add performance indexes for common query patterns

Revision ID: a9b8c7d6e5f4
Revises: efb333e0b501
Create Date: 2026-07-22 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "efb333e0b501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_cases_status", "cases", ["status"])
    op.create_index("ix_cases_priority", "cases", ["priority"])
    op.create_index("ix_cases_updated_at", "cases", ["updated_at"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_research_actions_status", "research_actions", ["status"])
    op.create_index(
        "ix_research_actions_action_type", "research_actions", ["action_type"]
    )
    op.create_index("ix_findings_content_hash", "findings", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_findings_content_hash", table_name="findings")
    op.drop_index("ix_research_actions_action_type", table_name="research_actions")
    op.drop_index("ix_research_actions_status", table_name="research_actions")
    op.drop_index("ix_invoices_status", table_name="invoices")
    op.drop_index("ix_cases_updated_at", table_name="cases")
    op.drop_index("ix_cases_priority", table_name="cases")
    op.drop_index("ix_cases_status", table_name="cases")
