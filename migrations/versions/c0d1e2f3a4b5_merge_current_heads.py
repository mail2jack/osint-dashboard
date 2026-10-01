"""Merge the parallel migration heads.

Revision ID: c0d1e2f3a4b5
Revises: b8c9d0e1f4a5, f4a5b6c7d8e0
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
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
    """Merge the heads and complete SQLite's identity-trigger path.

    The historical identity migration executes its PostgreSQL trigger DDL on
    PostgreSQL.  SQLite's trigger DDL is not reliably retained when Alembic
    traverses the parallel merge path, so the merge point makes the same
    protection explicit and idempotent for the test/dev dialect.
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_investigations_sequence_no_immutable
                BEFORE UPDATE OF sequence_no ON investigations
                FOR EACH ROW WHEN NEW.sequence_no IS NOT OLD.sequence_no
                BEGIN
                    SELECT RAISE(ABORT, 'investigation.sequence_no is immutable after issuance');
                END;
                """
            )
        )
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_investigations_case_id_immutable
                BEFORE UPDATE OF case_id ON investigations
                FOR EACH ROW WHEN NEW.case_id IS NOT OLD.case_id
                BEGIN
                    SELECT RAISE(ABORT, 'investigation.case_id is immutable after issuance');
                END;
                """
            )
        )
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_investigations_tenant_id_immutable
                BEFORE UPDATE OF tenant_id ON investigations
                FOR EACH ROW WHEN NEW.tenant_id IS NOT OLD.tenant_id
                BEGIN
                    SELECT RAISE(ABORT, 'investigation.tenant_id is immutable after issuance');
                END;
                """
            )
        )


def downgrade() -> None:
    """Split the merge point back into its two parent heads."""
