"""add credit notes tables

Revision ID: c1798b970286
Revises: f2a3b4c5d6e7
Create Date: 2026-06-19 15:07:22.985983

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1798b970286"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("credit_note_number", sa.String(length=50), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("credit_notes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_credit_notes_created_by"), ["created_by"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_credit_notes_credit_note_number"),
            ["credit_note_number"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_credit_notes_invoice_id"), ["invoice_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_credit_notes_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "credit_note_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("credit_note_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_item_id", sa.String(length=36), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("vat_total", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["credit_note_id"],
            ["credit_notes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["invoice_item_id"],
            ["invoice_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("credit_note_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_credit_note_items_credit_note_id"),
            ["credit_note_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_credit_note_items_invoice_item_id"),
            ["invoice_item_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_credit_note_items_tenant_id"), ["tenant_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("credit_note_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_credit_note_items_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_credit_note_items_invoice_item_id"))
        batch_op.drop_index(batch_op.f("ix_credit_note_items_credit_note_id"))
    op.drop_table("credit_note_items")
    with op.batch_alter_table("credit_notes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_credit_notes_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_credit_notes_invoice_id"))
        batch_op.drop_index(batch_op.f("ix_credit_notes_credit_note_number"))
        batch_op.drop_index(batch_op.f("ix_credit_notes_created_by"))
    op.drop_table("credit_notes")
