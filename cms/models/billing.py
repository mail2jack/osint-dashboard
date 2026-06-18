from datetime import datetime, timezone
from enum import Enum as PyEnum
import uuid

from ..models import db


class InvoiceStatus(PyEnum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    invoice_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    client_id = db.Column(
        db.String(36), db.ForeignKey("clients.id"), nullable=False, index=True
    )
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), index=True)

    issue_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default=InvoiceStatus.DRAFT.value)

    currency = db.Column(db.String(3), default="EUR")

    subtotal = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    vat_amount = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(15, 2), nullable=False, default=0)

    notes = db.Column(db.Text)
    terms = db.Column(db.Text)
    footer = db.Column(db.Text)

    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    sent_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    cancelled_reason = db.Column(db.Text)

    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    items = db.relationship(
        "InvoiceItem",
        backref="invoice",
        lazy="dynamic",
        order_by="InvoiceItem.sort_order",
        cascade="all, delete-orphan",
    )
    payments = db.relationship(
        "Payment",
        backref="invoice",
        lazy="dynamic",
        order_by="Payment.payment_date",
        cascade="all, delete-orphan",
    )
    creator = db.relationship("User", foreign_keys=[created_by])

    @staticmethod
    def generate_invoice_number() -> str:
        year = datetime.now().year
        prefix = f"FAC-{year}-"
        last = (
            Invoice.query.filter(Invoice.invoice_number.like(f"{prefix}%"))
            .order_by(Invoice.created_at.desc())
            .first()
        )
        if last:
            seq = int(last.invoice_number.split("-")[-1]) + 1
        else:
            seq = 1
        return f"{prefix}{seq:05d}"

    def recalculate(self) -> None:
        self.subtotal = 0
        self.vat_amount = 0
        for item in self.items:
            self.subtotal += item.total
            self.vat_amount += item.vat_total
        self.total = self.subtotal + self.vat_amount

    def mark_sent(self) -> None:
        self.status = InvoiceStatus.SENT.value
        self.sent_at = datetime.now(timezone.utc)

    def mark_paid(self) -> None:
        self.status = InvoiceStatus.PAID.value
        self.paid_at = datetime.now(timezone.utc)

    def mark_overdue(self) -> None:
        if self.status == InvoiceStatus.SENT.value:
            self.status = InvoiceStatus.OVERDUE.value

    def mark_cancelled(self, reason: str = "") -> None:
        self.status = InvoiceStatus.CANCELLED.value
        self.cancelled_at = datetime.now(timezone.utc)
        self.cancelled_reason = reason

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_number": self.invoice_number,
            "client_id": self.client_id,
            "case_id": self.case_id,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "status": self.status,
            "currency": self.currency,
            "subtotal": float(self.subtotal) if self.subtotal else 0,
            "vat_amount": float(self.vat_amount) if self.vat_amount else 0,
            "total": float(self.total) if self.total else 0,
            "notes": self.notes,
            "terms": self.terms,
            "footer": self.footer,
            "created_by": self.created_by,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "items": [i.to_dict() for i in self.items],
            "payments": [p.to_dict() for p in self.payments],
        }


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    invoice_id = db.Column(
        db.String(36), db.ForeignKey("invoices.id"), nullable=False, index=True
    )

    description = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Numeric(15, 2), nullable=False, default=1)
    unit_price = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    vat_rate = db.Column(db.Numeric(5, 2), nullable=False, default=21.00)
    total = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    vat_total = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    sort_order = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def recalculate(self) -> None:
        self.total = self.quantity * self.unit_price
        self.vat_total = self.total * (self.vat_rate / 100)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "description": self.description,
            "quantity": float(self.quantity) if self.quantity else 0,
            "unit_price": float(self.unit_price) if self.unit_price else 0,
            "vat_rate": float(self.vat_rate) if self.vat_rate else 0,
            "total": float(self.total) if self.total else 0,
            "vat_total": float(self.vat_total) if self.vat_total else 0,
            "sort_order": self.sort_order,
        }


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    invoice_id = db.Column(
        db.String(36), db.ForeignKey("invoices.id"), nullable=False, index=True
    )

    amount = db.Column(db.Numeric(15, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    payment_method = db.Column(db.String(50))
    reference = db.Column(db.String(200))
    notes = db.Column(db.Text)

    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "amount": float(self.amount) if self.amount else 0,
            "payment_date": self.payment_date.isoformat()
            if self.payment_date
            else None,
            "payment_method": self.payment_method,
            "reference": self.reference,
            "notes": self.notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
