"""Restore the case-number immutability trigger after SQLite table rebuilds.

Some later SQLite migrations rebuild ``cases`` with batch mode. SQLite does
not preserve table triggers during that rebuild, so the original ADR-0002
guard can disappear even though Alembic reports the schema at head.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "fk0e1f2a3b46"
down_revision: str | None = "fj0e1f2a3b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION fn_cases_case_number_immutable()
                RETURNS trigger AS $$
                BEGIN
                    IF NEW.case_number IS DISTINCT FROM OLD.case_number THEN
                        RAISE EXCEPTION
                            'case_number is immutable after issuance (ADR-0002 D4)'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                DROP TRIGGER IF EXISTS trg_cases_case_number_immutable ON cases;
                CREATE TRIGGER trg_cases_case_number_immutable
                BEFORE UPDATE ON cases
                FOR EACH ROW
                EXECUTE FUNCTION fn_cases_case_number_immutable();
                """
            )
        )
    else:
        bind.execute(sa.text("DROP TRIGGER IF EXISTS trg_cases_case_number_immutable"))
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER trg_cases_case_number_immutable
                BEFORE UPDATE OF case_number ON cases
                FOR EACH ROW
                WHEN NEW.case_number IS NOT OLD.case_number
                BEGIN
                    SELECT RAISE(ABORT,
                        'case_number is immutable after issuance (ADR-0002 D4)');
                END;
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_cases_case_number_immutable ON cases"
            )
        )
        bind.execute(sa.text("DROP FUNCTION IF EXISTS fn_cases_case_number_immutable()"))
    else:
        bind.execute(sa.text("DROP TRIGGER IF EXISTS trg_cases_case_number_immutable"))
