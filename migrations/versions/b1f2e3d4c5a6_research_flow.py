"""research flow: findings verification state (status, verified_by, verified_at)

ADR-0001 PR5 (D1.5): findings get an explicit lifecycle so candidates can be
promoted to verified facts or rejected. The legacy `verified` boolean is kept
as a readable mirror; `status` becomes the source of truth
(candidate|verified|rejected|superseded). Existing verified rows are backfilled
to `status='verified'` with their last-updated timestamp.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b1f2e3d4c5a6"
down_revision: str | None = "f4e5d6c7b8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("findings") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("verified_by", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_findings_status", ["status"])
        batch_op.create_index("ix_findings_verified_by", ["verified_by"])
        batch_op.create_foreign_key(
            "fk_findings_verified_by",
            "users",
            ["verified_by"],
            ["id"],
        )

    # Backfill the lifecycle for existing findings: verified rows keep their
    # last-updated timestamp as verification moment, everything else is a
    # candidate. The bare `WHERE verified` predicate is dialect-agnostic
    # (BOOLEAN on PostgreSQL, truthy integer on SQLite).
    op.execute(
        "UPDATE findings SET status = 'verified', "
        "verified_at = COALESCE(updated_at, created_at) "
        "WHERE verified AND status IS NULL"
    )
    op.execute("UPDATE findings SET status = 'candidate' WHERE status IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("findings") as batch_op:
        batch_op.drop_constraint("fk_findings_verified_by", type_="foreignkey")
        batch_op.drop_index("ix_findings_verified_by")
        batch_op.drop_index("ix_findings_status")
        batch_op.drop_column("status")
        batch_op.drop_column("verified_by")
        batch_op.drop_column("verified_at")
