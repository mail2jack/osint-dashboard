"""Increase invoice_items.description length to 2000.

Google Dork action payloads (full dork query JSON) can exceed the old
String(500) limit, crashing auto-invoicing with StringDataRightTruncation
(e.g. "Google Dork Search" on case-wide scope). The service layer now also
truncates before insert; this column bump gives generous headroom for
legitimately descriptive lines.

WARNING (downgrade): narrowing back to VARCHAR(500) blocks with a controlled
error as soon as any row exceeds 500 chars. Manually decide how to handle such
rows (truncate with the service's normalize_description()/500 or keep data)
before retrying the downgrade. The column is intentionally NOT indexed:
truncation-length only needs VARCHAR(2000), not an index.

Revision ID: d5e6f7a8b9c0
Revises: f8a9b0c1d2e3
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE invoice_items ALTER COLUMN description TYPE VARCHAR(2000)"
            )
        )
    else:
        with op.batch_alter_table("invoice_items") as batch:
            batch.alter_column(
                "description", existing_type=sa.String(500), type_=sa.String(2000)
            )


def downgrade() -> None:
    # Truncating back to 500 could destroy data written at the larger size;
    # the service truncates to 2000 so downgrade would need a data pass.
    # Refuse the downgrade if any row still exceeds 500 (on-postgres the ALTER
    # itself would truncate; here we stop before any DDL with a clear error).
    bind = op.get_bind()
    length_fn = "char_length" if bind.dialect.name == "postgresql" else "length"
    bad = bind.execute(
        sa.text(
            f"SELECT count(*) FROM invoice_items WHERE {length_fn}(description) > 500"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"Cannot downgrade description width: {bad} line(s) exceed 500; "
            "truncate those rows first or keep data (see migration docstring)"
        )
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE invoice_items ALTER COLUMN description TYPE VARCHAR(500)"
            )
        )
    else:
        with op.batch_alter_table("invoice_items") as batch:
            batch.alter_column(
                "description", existing_type=sa.String(2000), type_=sa.String(500)
            )
