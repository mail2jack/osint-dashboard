"""add invoice billing tables

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-06-08 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_number", sa.String(50), nullable=False, index=True, unique=True
        ),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), index=True),
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("subtotal", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("vat_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("terms", sa.Text, nullable=True),
        sa.Column("footer", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), index=True),
        sa.Column("sent_at", sa.DateTime, nullable=True),
        sa.Column("paid_at", sa.DateTime, nullable=True),
        sa.Column("cancelled_at", sa.DateTime, nullable=True),
        sa.Column("cancelled_reason", sa.Text, nullable=True),
        sa.Column(
            "is_deleted", sa.Boolean, nullable=False, server_default="false", index=True
        ),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "invoice_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(36),
            sa.ForeignKey("invoices.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False, server_default="21.00"),
        sa.Column("total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("vat_total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(36),
            sa.ForeignKey("invoices.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("payment_date", sa.Date, nullable=False),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), index=True),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_index("ix_invoices_client_status", "invoices", ["client_id", "status"])
    op.create_index("ix_invoices_due_date_status", "invoices", ["due_date", "status"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("invoice_items")
    op.drop_table("invoices")
