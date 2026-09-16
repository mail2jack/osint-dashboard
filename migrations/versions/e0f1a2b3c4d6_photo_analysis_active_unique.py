"""Partial unique index: one active photo_analysis per tenant/case/subject.

Concurrency invariant for the photo-analysis upload route. A query-before-
insert alone cannot stop two simultaneous uploads for the same
``(tenant_id, case_id, subject_id)`` from both creating a ``pending``/
``running`` row. This partial unique index is the DB-level guard:

    (tenant_id, case_id, COALESCE(subject_id, ''))
        unique where action_type='photo_analysis'
               and status in ('pending', 'running')

``subject_id`` is nullable (case-wide actions use NULL); ``COALESCE`` in the
index expression makes *two* case-wide active actions just as unique as two
subject-scoped ones. The route maps the resulting IntegrityError to a 409
and cleans up the transient upload file.

Revision ID: e0f1a2b3c4d6
Revises: d5e6f7a8b9c0
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d6"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_research_actions_active_photo_analysis"
_INDEX_SQL = (
    "CREATE UNIQUE INDEX {name}"
    " ON research_actions (tenant_id, case_id, COALESCE(subject_id, ''))"
    " WHERE action_type = 'photo_analysis'"
    "   AND status IN ('pending', 'running')"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in ("postgresql", "sqlite"):
        raise RuntimeError(f"Unsupported dialect {bind.dialect.name} for {_INDEX_NAME}")
    op.execute(sa.text(_INDEX_SQL.format(name=_INDEX_NAME)))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
