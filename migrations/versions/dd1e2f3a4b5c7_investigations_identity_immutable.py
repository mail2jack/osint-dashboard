"""ADR-0002 PR3 P1: make investigations identity immutable at the DB level

The referenced ``human_number`` of an investigation is derived from the
immutable case number plus the immutably issued ``sequence_no``. Moving an
investigation to another case (or reassigning its tenant) would silently
change that issued reference and undermine the audit trail, so it must be
blocked at the database level — not only in the application routes.

The existing migration ``bb1c2d3e4f5a7`` already blocks ``sequence_no``
updates (and ``cases.case_number`` updates) via BEFORE UPDATE triggers. This
migration extends the same protection to:

- ``investigations.sequence_no``  (existing protection preserved, untouched)
- ``investigations.case_id``      (NEW)
- ``investigations.tenant_id``    (NEW)

PostgreSQL raises with the ``check_violation`` SQLSTATE (23514) so callers
see an ``IntegrityError``, matching the ``ABORT`` semantics of the SQLite
trigger. Creating a fresh row is unaffected — the triggers only fire on
UPDATE of the protected columns.

Revision ID: dd1e2f3a4b5c7
Revises: bb1c2d3e4f5a7
Create Date: 2026-08-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dd1e2f3a4b5c7"
down_revision: str | None = "bb1c2d3e4f5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_identity_triggers() -> None:
    """Block UPDATEs to ``case_id``/``tenant_id`` on investigations.

    ``sequence_no`` immutability comes from the ``bb1c2d3e4f5a7`` trigger and
    is deliberately left alone so the migrations stay independent.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION fn_investigations_identity_immutable()
                RETURNS trigger AS $$
                BEGIN
                    IF NEW.case_id IS DISTINCT FROM OLD.case_id THEN
                        RAISE EXCEPTION
                            'investigation.case_id is immutable after issuance (ADR-0002 PR3 P1)'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
                        RAISE EXCEPTION
                            'investigation.tenant_id is immutable after issuance (ADR-0002 PR3 P1)'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        bind.execute(
            sa.text(
                """
                DROP TRIGGER IF EXISTS trg_investigations_identity_immutable
                    ON investigations;
                CREATE TRIGGER trg_investigations_identity_immutable
                BEFORE UPDATE ON investigations
                FOR EACH ROW
                EXECUTE FUNCTION fn_investigations_identity_immutable();
                """
            )
        )
    else:
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER trg_investigations_case_id_immutable
                BEFORE UPDATE OF case_id ON investigations
                FOR EACH ROW
                WHEN NEW.case_id IS NOT OLD.case_id
                BEGIN
                    SELECT RAISE(ABORT,
                        'investigation.case_id is immutable after issuance (ADR-0002 PR3 P1)');
                END;
                """
            )
        )
        bind.execute(
            sa.text(
                """
                CREATE TRIGGER trg_investigations_tenant_id_immutable
                BEFORE UPDATE OF tenant_id ON investigations
                FOR EACH ROW
                WHEN NEW.tenant_id IS NOT OLD.tenant_id
                BEGIN
                    SELECT RAISE(ABORT,
                        'investigation.tenant_id is immutable after issuance (ADR-0002 PR3 P1)');
                END;
                """
            )
        )


def _drop_identity_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_investigations_identity_immutable "
                "ON investigations"
            )
        )
        bind.execute(
            sa.text("DROP FUNCTION IF EXISTS fn_investigations_identity_immutable()")
        )
    else:
        bind.execute(
            sa.text("DROP TRIGGER IF EXISTS trg_investigations_case_id_immutable")
        )
        bind.execute(
            sa.text("DROP TRIGGER IF EXISTS trg_investigations_tenant_id_immutable")
        )


def upgrade() -> None:
    _create_identity_triggers()


def downgrade() -> None:
    _drop_identity_triggers()