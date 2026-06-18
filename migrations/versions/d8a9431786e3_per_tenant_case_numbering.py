"""per_tenant_case_numbering

Revision ID: d8a9431786e3
Revises: 4b160010d177
Create Date: 2026-06-10 10:34:31.142301

"""

from typing import Sequence, Union

from alembic import op


revision: str = "d8a9431786e3"
down_revision: Union[str, Sequence[str], None] = "4b160010d177"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Drop the unique index on case_number, add composite (tenant_id, case_number)
    if dialect == "sqlite":
        with op.batch_alter_table("cases") as batch_op:
            batch_op.drop_index("ix_cases_case_number")
            batch_op.create_unique_constraint(
                "uq_tenant_case_number", ["tenant_id", "case_number"]
            )
    elif dialect == "postgresql":
        op.drop_index("ix_cases_case_number", table_name="cases")
        op.create_unique_constraint(
            "uq_tenant_case_number", "cases", ["tenant_id", "case_number"]
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("cases") as batch_op:
            batch_op.drop_constraint("uq_tenant_case_number", type_="unique")
            batch_op.create_index("ix_cases_case_number", ["case_number"], unique=True)
    elif dialect == "postgresql":
        op.drop_constraint("uq_tenant_case_number", "cases", type_="unique")
        op.create_index("ix_cases_case_number", "cases", ["case_number"], unique=True)
