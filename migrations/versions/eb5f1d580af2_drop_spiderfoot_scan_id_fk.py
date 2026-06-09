"""drop spiderfoot_scan_id fk from osint_searches

Revision ID: eb5f1d580af2
Revises: e5f6a7b8c9d0
Create Date: 2026-06-09 10:54:56.152379

"""

from collections.abc import Sequence

from alembic import op

revision: str = "eb5f1d580af2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_fk(table: str, fk_name: str) -> bool:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    fks = inspector.get_foreign_keys(table)
    return any(fk.get("name") == fk_name for fk in fks)


def upgrade() -> None:
    if _has_fk("osint_searches", "osint_searches_spiderfoot_scan_id_fkey"):
        with op.batch_alter_table("osint_searches", schema=None) as batch_op:
            batch_op.drop_constraint(
                "osint_searches_spiderfoot_scan_id_fkey", type_="foreignkey"
            )


def downgrade() -> None:
    with op.batch_alter_table("osint_searches", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "osint_searches_spiderfoot_scan_id_fkey",
            "spiderfoot_scans",
            ["spiderfoot_scan_id"],
            ["id"],
        )
