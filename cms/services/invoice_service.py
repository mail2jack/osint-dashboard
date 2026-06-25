"""
Invoice service — PDF generation, status flow, helpers.
"""

import logging
from datetime import datetime

import flask
from weasyprint import HTML as WPHTML

from ..models import (
    db,
    Invoice,
    InvoiceItem,
    Payment,
    Client,
    CreditNote,
    CreditNoteItem,
)

logger = logging.getLogger(__name__)

DEFAULT_TERMS = (
    "Betaling dient binnen 30 dagen na factuurdatum te zijn voldaan.\n"
    "Bij niet-tijdige betaling zijn wij gerechtigd rente in rekening te brengen."
)
DEFAULT_FOOTER = "Alle rechten voorbehouden. Iveras OSINT Dashboard."


def generate_invoice_pdf(invoice: Invoice) -> bytes:
    """Render invoice as PDF bytes via WeasyPrint."""
    client = db.session.get(Client, invoice.client_id)
    items = (
        InvoiceItem.query.filter_by(invoice_id=invoice.id)
        .order_by(InvoiceItem.sort_order)
        .all()
    )
    payments = (
        Payment.query.filter_by(invoice_id=invoice.id)
        .order_by(Payment.payment_date)
        .all()
    )

    html = flask.render_template(
        "cms/invoicing/invoice_pdf.html",
        invoice=invoice,
        client=client,
        items=items,
        payments=payments,
        now=datetime.now,
    )
    try:
        return WPHTML(string=html).write_pdf()
    except Exception as e:
        logger.error("Invoice PDF generation failed: %s", e)
        raise


def recalculate_invoice(invoice: Invoice) -> None:
    """Recalculate subtotal, vat_amount, and total from items."""
    invoice.subtotal = 0
    invoice.vat_amount = 0
    for item in InvoiceItem.query.filter_by(invoice_id=invoice.id).all():
        item.recalculate()
        invoice.subtotal += item.total
        invoice.vat_amount += item.vat_total
    invoice.total = invoice.subtotal + invoice.vat_amount
    db.session.commit()


def mark_invoice_sent(invoice: Invoice) -> None:
    invoice.mark_sent()
    db.session.commit()


def mark_invoice_paid(invoice: Invoice) -> None:
    invoice.mark_paid()
    db.session.commit()


def mark_invoice_overdue(invoice: Invoice) -> None:
    invoice.mark_overdue()
    db.session.commit()


def mark_invoice_cancelled(invoice: Invoice, reason: str = "") -> None:
    invoice.mark_cancelled(reason)
    db.session.commit()


def generate_credit_note_pdf(cn: CreditNote) -> bytes:
    """Render credit note as PDF bytes via WeasyPrint."""
    invoice = db.session.get(Invoice, cn.invoice_id) if cn.invoice_id else None
    client = db.session.get(Client, invoice.client_id) if invoice else None
    items = (
        CreditNoteItem.query.filter_by(credit_note_id=cn.id)
        .order_by(CreditNoteItem.sort_order)
        .all()
    )

    html = flask.render_template(
        "cms/invoicing/credit_note_pdf.html",
        cn=cn,
        invoice=invoice,
        client=client,
        items=items,
        now=datetime.now,
    )
    try:
        return WPHTML(string=html).write_pdf()
    except Exception as e:
        logger.error("Credit note PDF generation failed: %s", e)
        raise
