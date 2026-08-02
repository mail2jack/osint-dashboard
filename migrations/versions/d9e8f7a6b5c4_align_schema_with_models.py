"""align_schema_with_models

Adds schema objects declared in the models but missing from the database:

- cases.case_number index (Case.case_number has index=True)
- tenants.join_code unique index (Tenant.join_code has unique=True, index=True;
  replaces the legacy uq_tenants_join_code named constraint)
- social_accounts.finding_id foreign key to findings.id
  (SocialAccount.finding_id declares db.ForeignKey)

Revision ID: d9e8f7a6b5c4
Revises: b3c4d5e6f7a8
Create Date: 2026-08-02 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d9e8f7a6b5c4"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_cases_case_number", "cases", ["case_number"])

    # Replace the legacy named unique constraint with a unique index so the
    # schema matches the model declaration (unique=True + index=True).
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_constraint("uq_tenants_join_code", type_="unique")
    op.create_index("ix_tenants_join_code", "tenants", ["join_code"], unique=True)

    with op.batch_alter_table("social_accounts", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_social_accounts_finding_id", "findings", ["finding_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("social_accounts", schema=None) as batch_op:
        batch_op.drop_constraint("fk_social_accounts_finding_id", type_="foreignkey")

    op.drop_index("ix_tenants_join_code", table_name="tenants")
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_tenants_join_code", ["join_code"])

    op.drop_index("ix_cases_case_number", table_name="cases")
