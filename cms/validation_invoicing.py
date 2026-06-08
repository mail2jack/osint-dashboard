"""Pydantic validation schemas for invoicing routes."""

from typing import Any
from pydantic import BaseModel


class CreateInvoiceSchema(BaseModel):
    client_id: str = ""
    case_id: str | None = None
    issue_date: str = ""
    due_date: str = ""
    currency: str = "EUR"
    notes: str | None = None
    terms: str | None = None
    footer: str | None = None
    items: Any = None  # JSON string of items array


class EditInvoiceSchema(BaseModel):
    issue_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    notes: str | None = None
    terms: str | None = None
    footer: str | None = None
    status: str | None = None
    items: Any = None


class AddPaymentSchema(BaseModel):
    amount: Any = ""
    payment_date: str = ""
    payment_method: str | None = None
    reference: str | None = None
    notes: str | None = None


class CancelInvoiceSchema(BaseModel):
    reason: str = ""
