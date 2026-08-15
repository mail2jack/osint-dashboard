"""
Invoice service — PDF generation, status flow, helpers, and auto-invoicing.
"""

import logging
from datetime import datetime, date

import flask


from ..models import (
    db,
    Invoice,
    InvoiceItem,
    Payment,
    Client,
    Case,
    CreditNote,
    CreditNoteItem,
    ServiceRate,
    ResearchAction,
    InvoiceStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_TERMS = (
    "Payment must be made within 30 days of the invoice date.\n"
    "Bij niet-tijdige betaling zijn wij gerechtigd rente in rekening te brengen."
)
DEFAULT_FOOTER = "Alle rechten voorbehouden. Iveras OSINT Dashboard."


# ---------------------------------------------------------------------------
# Auto-invoicing
# ---------------------------------------------------------------------------


def _get_rate(service_type: str) -> ServiceRate | None:
    return ServiceRate.query.filter_by(
        service_type=service_type, is_active=True
    ).first()


def _ensure_draft_invoice(client_id: str, tenant_id: str) -> Invoice | None:
    """Find an existing draft invoice for this client this month, or create one."""
    today = date.today()
    invoice = Invoice.query.filter_by(
        client_id=client_id, status=InvoiceStatus.DRAFT.value
    ).first()
    if invoice:
        return invoice
    invoice = Invoice(
        client_id=client_id,
        case_id=None,
        tenant_id=tenant_id,
        issue_date=today,
        due_date=date(today.year, today.month + 1, today.day)
        if today.month < 12
        else date(today.year + 1, 1, today.day),
        status=InvoiceStatus.DRAFT.value,
        subtotal=0,
        vat_amount=0,
        total=0,
    )
    invoice.invoice_number = Invoice.generate_invoice_number()
    db.session.add(invoice)
    db.session.flush()
    return invoice


def _add_invoice_line(
    invoice: Invoice,
    description: str,
    rate: ServiceRate,
    tenant_id: str | None = None,
    quantity: int = 1,
) -> None:
    item = InvoiceItem(
        invoice_id=invoice.id,
        tenant_id=tenant_id or invoice.tenant_id,
        description=description,
        quantity=quantity,
        unit_price=rate.unit_price,
        vat_rate=rate.vat_rate,
    )
    item.recalculate()
    db.session.add(item)
    db.session.flush()
    invoice.recalculate()
    db.session.commit()


def seed_service_rates() -> None:
    """Seed default service rates if none exist."""
    if ServiceRate.query.first():
        return
    from cms.models import User

    admin = User.query.filter_by(role="admin").order_by(User.created_at).first()
    tenant_id = admin.tenant_id if admin else None
    defaults = [
        ("case_creation", "Create investigation", 75, 21.00),
        ("research_action", "Search action (per platform)", 15, 21.00),
        ("pv_creation", "Draft official report", 150, 21.00),
    ]
    for stype, desc, price, vat in defaults:
        rate = ServiceRate(
            tenant_id=tenant_id,
            service_type=stype,
            description=desc,
            unit_price=price,
            vat_rate=vat,
        )
        db.session.add(rate)
    db.session.commit()


def auto_invoice_case_created(case: Case) -> None:
    """Add invoice line for case creation."""
    if not case.client_id:
        return
    client = db.session.get(Client, case.client_id)
    if not client:
        return
    rate = _get_rate("case_creation")
    if not rate:
        return
    invoice = _ensure_draft_invoice(case.client_id, client.tenant_id)
    _add_invoice_line(
        invoice,
        f"Onderzoek: {case.title or case.case_number}",
        rate,
    )


def auto_invoice_action_completed(action: ResearchAction) -> None:
    """Add invoice line for a completed research action."""
    if not action.case_id:
        return
    case = db.session.get(Case, action.case_id)
    if not case or not case.client_id:
        return
    client = db.session.get(Client, case.client_id)
    if not client:
        return
    rate = _get_rate("research_action")
    if not rate:
        return
    invoice = _ensure_draft_invoice(case.client_id, client.tenant_id)
    data_str = f" ({action.data_value})" if action.data_value else ""
    _add_invoice_line(
        invoice,
        f"Zoekactie {action.label or action.action_type}: {action.case.case_number}{data_str}",
        rate,
    )


def auto_invoice_pv_created(case: Case) -> None:
    """Add invoice line when a process report (PV) is first created."""
    if not case.client_id:
        return
    client = db.session.get(Client, case.client_id)
    if not client:
        return
    rate = _get_rate("pv_creation")
    if not rate:
        return
    invoice = _ensure_draft_invoice(case.client_id, client.tenant_id)
    _add_invoice_line(
        invoice,
        f"Proces verbaal: {case.case_number}",
        rate,
    )


def generate_invoice_pdf(invoice: Invoice) -> bytes:
    """Render invoice as PDF bytes via WeasyPrint."""
    client = db.session.get(Client, invoice.client_id)
    # Render with autoflush off: the item/payment queries below would autoflush
    # and re-encrypt the freshly decrypted client, showing ciphertext in the PDF.
    with db.session.no_autoflush:
        if client:
            client.decrypt_naw()
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
        from weasyprint import HTML as WPHTML

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
        from weasyprint import HTML as WPHTML

        return WPHTML(string=html).write_pdf()
    except Exception as e:
        logger.error("Credit note PDF generation failed: %s", e)
        raise
