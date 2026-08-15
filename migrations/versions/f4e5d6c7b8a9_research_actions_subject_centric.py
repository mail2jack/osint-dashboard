"""research_actions subject-centric (subject_id, target_kind, target_snapshot)

ADR-0001 PR4 (D1.4): actions get an explicit target subject. subject_id is
nullable only to express an explicit case-wide scope; a non-null value must
always be a subject linked to the action's case (enforced by the API layer).
target_snapshot captures the normalized input at creation so an action stays
reproducible even when the subject changes later.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "f4e5d6c7b8a9"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("research_actions") as batch_op:
        batch_op.add_column(sa.Column("subject_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("target_kind", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("target_snapshot", sa.Text(), nullable=True))
        batch_op.create_index("ix_research_actions_subject_id", ["subject_id"])
        batch_op.create_foreign_key(
            "fk_research_actions_subject_id",
            "subjects",
            ["subject_id"],
            ["id"],
        )

    # Existing actions predate the subject-centric model: they ran case-wide.
    op.execute(
        "UPDATE research_actions SET target_kind = 'case' WHERE target_kind IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("research_actions") as batch_op:
        batch_op.drop_constraint("fk_research_actions_subject_id", type_="foreignkey")
        batch_op.drop_index("ix_research_actions_subject_id")
        batch_op.drop_column("subject_id")
        batch_op.drop_column("target_kind")
        batch_op.drop_column("target_snapshot")
