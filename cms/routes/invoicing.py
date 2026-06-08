"""
Invoice/billing routes — CRUD, PDF, send, mark paid/cancelled.
"""

import json
import logging
from datetime import date

import flask
from flask import request, jsonify, render_template, abort, redirect, url_for, flash
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Invoice, InvoiceItem, Payment, Client, Case, AuditLog
from ..auth import roles_required, admin_required
from ..validation_invoicing import (
    CreateInvoiceSchema,
    EditInvoiceSchema,
    AddPaymentSchema,
)

logger = logging.getLogger(__name__)


def _invoice_to_dict(inv: Invoice) -> dict:
    client = db.session.get(Client, inv.client_id)
    case = db.session.get(Case, inv.case_id) if inv.case_id else None
    creator = inv.creator
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "client_name": client.name if client else "?",
        "case_number": case.case_number if case else None,
        "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "status": inv.status,
        "currency": inv.currency,
        "subtotal": float(inv.subtotal),
        "vat_amount": float(inv.vat_amount),
        "total": float(inv.total),
        "created_by": creator.full_name if creator else None,
        "sent_at": inv.sent_at.isoformat() if inv.sent_at else None,
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }


# ── List ────────────────────────────────────────────────────────────────
@cms_bp.route("/invoices")
@login_required
def invoice_list():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)
    status_filter = request.args.get("status", "")
    client_filter = request.args.get("client_id", "")
    search = request.args.get("q", "").strip()

    q = Invoice.query.filter(Invoice.is_deleted == False)

    if status_filter:
        q = q.filter(Invoice.status == status_filter)
    if client_filter:
        q = q.filter(Invoice.client_id == client_filter)
    if search:
        like = f"%{search}%"
        q = q.filter(
            db.or_(
                Invoice.invoice_number.ilike(like),
                Invoice.notes.ilike(like),
            )
        )

    q = q.order_by(Invoice.created_at.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    clients = (
        Client.query.filter(Client.is_deleted == False, Client.is_active == True)
        .order_by(Client.name)
        .all()
    )

    invoices_data = [_invoice_to_dict(inv) for inv in pagination.items]
    total_paid = sum(
        float(inv.total)
        for inv in Invoice.query.filter(
            Invoice.is_deleted == False, Invoice.status == "paid"
        ).all()
    )
    total_outstanding = sum(
        float(inv.total)
        for inv in Invoice.query.filter(
            Invoice.is_deleted == False,
            Invoice.status.in_(["sent", "overdue"]),
        ).all()
    )

    return render_template(
        "cms/invoicing/list.html",
        invoices=invoices_data,
        pagination=pagination,
        clients=clients,
        status_filter=status_filter,
        client_filter=client_filter,
        search=search,
        total_paid=total_paid,
        total_outstanding=total_outstanding,
    )


# ── Create ──────────────────────────────────────────────────────────────
@cms_bp.route("/invoices/create", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior")
def invoice_create():
    clients = (
        Client.query.filter(Client.is_deleted == False, Client.is_active == True)
        .order_by(Client.name)
        .all()
    )

    if request.method == "GET":
        preselected = request.args.get("client_id", "")
        preselected_case = request.args.get("case_id", "")

        # Fetch cases for dropdown, filtered by client if preselected
        cases_q = Case.query.filter(Case.is_deleted == False)
        if preselected:
            cases_q = cases_q.filter(Case.client_id == preselected)
        cases = cases_q.order_by(Case.case_number.desc()).limit(100).all()

        # Build client → cases lookup for JS filter
        all_cases = (
            Case.query.filter(Case.is_deleted == False)
            .order_by(Case.case_number.desc())
            .limit(200)
            .all()
        )
        cases_by_client: dict[str, list[dict]] = {}
        for c in all_cases:
            cid = c.client_id
            if cid not in cases_by_client:
                cases_by_client[cid] = []
            cases_by_client[cid].append(
                {"id": c.id, "case_number": c.case_number, "title": c.title}
            )

        import json as _json

        cases_data_json = _json.dumps(cases_by_client)

        return render_template(
            "cms/invoicing/create.html",
            clients=clients,
            cases=cases,
            cases_data_json=cases_data_json,
            preselected=preselected,
            preselected_case=preselected_case,
        )

    schema = CreateInvoiceSchema(**request.form.to_dict())
    if not schema.client_id or not schema.issue_date or not schema.due_date:
        flash("Client, factuurdatum en vervaldatum zijn verplicht.", "error")
        return redirect(url_for("cms.invoice_create"))

    try:
        issue_date = date.fromisoformat(schema.issue_date)
        due_date = date.fromisoformat(schema.due_date)
    except ValueError:
        flash("Ongeldige datumnotatie.", "error")
        return redirect(url_for("cms.invoice_create"))

    invoice = Invoice(
        invoice_number=Invoice.generate_invoice_number(),
        client_id=schema.client_id,
        case_id=schema.case_id or None,
        issue_date=issue_date,
        due_date=due_date,
        currency=schema.currency or "EUR",
        notes=schema.notes or None,
        terms=schema.terms or None,
        footer=schema.footer or None,
        created_by=current_user.id,
    )
    db.session.add(invoice)
    db.session.flush()

    # Parse items JSON
    items_data = schema.items
    if isinstance(items_data, str):
        try:
            items_data = json.loads(items_data)
        except (json.JSONDecodeError, TypeError):
            items_data = []
    if isinstance(items_data, list):
        for i, item in enumerate(items_data):
            inv_item = InvoiceItem(
                invoice_id=invoice.id,
                description=item.get("description", ""),
                quantity=float(item.get("quantity", 1)),
                unit_price=float(item.get("unit_price", 0)),
                vat_rate=float(item.get("vat_rate", 21.00)),
                sort_order=i,
            )
            inv_item.recalculate()
            db.session.add(inv_item)

    invoice.recalculate()
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.remote_addr,
        description=f"Created invoice {invoice.invoice_number}",
    )
    db.session.commit()
    flash(f"Factuur {invoice.invoice_number} aangemaakt.", "success")
    return redirect(url_for("cms.invoice_view", invoice_id=invoice.id))


# ── View ────────────────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>")
@login_required
def invoice_view(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    client = db.session.get(Client, invoice.client_id) or abort(404)
    items = (
        InvoiceItem.query.filter_by(invoice_id=invoice_id)
        .order_by(InvoiceItem.sort_order)
        .all()
    )
    payments = (
        Payment.query.filter_by(invoice_id=invoice_id)
        .order_by(Payment.payment_date)
        .all()
    )
    case = db.session.get(Case, invoice.case_id) if invoice.case_id else None

    return render_template(
        "cms/invoicing/view.html",
        invoice=invoice,
        client=client,
        items=items,
        payments=payments,
        case=case,
    )


# ── Edit ────────────────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "senior")
def invoice_edit(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    if invoice.status in ("paid", "cancelled"):
        flash("Een betaalde of geannuleerde factuur kan niet worden bewerkt.", "error")
        return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))

    clients = (
        Client.query.filter(Client.is_deleted == False, Client.is_active == True)
        .order_by(Client.name)
        .all()
    )

    if request.method == "GET":
        return render_template(
            "cms/invoicing/edit.html",
            invoice=invoice,
            clients=clients,
        )

    schema = EditInvoiceSchema(**request.form.to_dict())

    if schema.issue_date:
        try:
            invoice.issue_date = date.fromisoformat(schema.issue_date)
        except ValueError:
            flash("Ongeldige factuurdatum.", "error")
            return redirect(url_for("cms.invoice_edit", invoice_id=invoice_id))
    if schema.due_date:
        try:
            invoice.due_date = date.fromisoformat(schema.due_date)
        except ValueError:
            flash("Ongeldige vervaldatum.", "error")
            return redirect(url_for("cms.invoice_edit", invoice_id=invoice_id))
    if schema.currency:
        invoice.currency = schema.currency
    if schema.notes is not None:
        invoice.notes = schema.notes
    if schema.terms is not None:
        invoice.terms = schema.terms
    if schema.footer is not None:
        invoice.footer = schema.footer

    # Replace items
    items_data = schema.items
    if isinstance(items_data, str):
        try:
            items_data = json.loads(items_data)
        except (json.JSONDecodeError, TypeError):
            items_data = None
    if isinstance(items_data, list):
        InvoiceItem.query.filter_by(invoice_id=invoice_id).delete()
        for i, item in enumerate(items_data):
            inv_item = InvoiceItem(
                invoice_id=invoice.id,
                description=item.get("description", ""),
                quantity=float(item.get("quantity", 1)),
                unit_price=float(item.get("unit_price", 0)),
                vat_rate=float(item.get("vat_rate", 21.00)),
                sort_order=i,
            )
            inv_item.recalculate()
            db.session.add(inv_item)

    invoice.recalculate()
    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.remote_addr,
        description=f"Edited invoice {invoice.invoice_number}",
    )
    db.session.commit()
    flash(f"Factuur {invoice.invoice_number} bijgewerkt.", "success")
    return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))


# ── PDF ─────────────────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    from ..services.invoice_service import generate_invoice_pdf

    try:
        pdf_data = generate_invoice_pdf(invoice)
    except Exception as e:
        logger.error("PDF generation failed for invoice %s: %s", invoice_id, e)
        flash("PDF generatie mislukt.", "error")
        return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))

    safe_num = invoice.invoice_number.replace("/", "-")
    return flask.Response(
        pdf_data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={safe_num}.pdf"},
    )


# ── Send (mark sent) ────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/send", methods=["POST"])
@login_required
@roles_required("admin", "senior")
def invoice_send(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    if invoice.status != "draft":
        flash("Alleen concept-facturen kunnen worden verzonden.", "error")
        return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))
    invoice.mark_sent()
    AuditLog.log(
        user_id=current_user.id,
        action="send",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.remote_addr,
        description=f"Sent invoice {invoice.invoice_number}",
    )
    db.session.commit()
    flash(f"Factuur {invoice.invoice_number} gemarkeerd als verzonden.", "success")
    return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))


# ── Mark Paid ───────────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/mark-paid", methods=["POST"])
@login_required
@roles_required("admin", "senior")
def invoice_mark_paid(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    if invoice.status in ("paid", "cancelled"):
        flash("Factuur is al betaald of geannuleerd.", "error")
        return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))

    data = request.get_json(silent=True) or {}
    if data:
        schema = AddPaymentSchema(**data)
        try:
            payment_date = (
                date.fromisoformat(schema.payment_date)
                if schema.payment_date
                else date.today()
            )
        except ValueError:
            payment_date = date.today()
        payment = Payment(
            invoice_id=invoice_id,
            amount=float(schema.amount) if schema.amount else float(invoice.total),
            payment_date=payment_date,
            payment_method=schema.payment_method or "transfer",
            reference=schema.reference or None,
            notes=schema.notes or None,
            created_by=current_user.id,
        )
        db.session.add(payment)

    invoice.mark_paid()
    AuditLog.log(
        user_id=current_user.id,
        action="mark_paid",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.remote_addr,
        description=f"Marked invoice {invoice.invoice_number} paid",
    )
    db.session.commit()

    if data:
        flash(
            f"Betaling geregistreerd en factuur {invoice.invoice_number} als betaald gemarkeerd.",
            "success",
        )
    else:
        flash(f"Factuur {invoice.invoice_number} als betaald gemarkeerd.", "success")
    return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))


# ── Cancel ──────────────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/cancel", methods=["POST"])
@login_required
@roles_required("admin", "senior")
def invoice_cancel(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    if invoice.status in ("paid", "cancelled"):
        flash("Factuur is al betaald of geannuleerd.", "error")
        return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))

    data = request.get_json(silent=True) or {}
    invoice.mark_cancelled(data.get("reason", ""))
    AuditLog.log(
        user_id=current_user.id,
        action="cancel",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.remote_addr,
        description=f"Cancelled invoice {invoice.invoice_number}",
    )
    db.session.commit()
    flash(f"Factuur {invoice.invoice_number} geannuleerd.", "success")
    return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))


# ── Delete ──────────────────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/delete", methods=["POST"])
@login_required
@admin_required
def invoice_delete(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    invoice.soft_delete()
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="invoice",
        entity_id=invoice.id,
        ip_address=request.remote_addr,
        description=f"Deleted invoice {invoice.invoice_number}",
    )
    db.session.commit()
    flash(f"Factuur {invoice.invoice_number} verwijderd.", "success")
    return redirect(url_for("cms.invoice_list"))


# ── API: quick status counts ────────────────────────────────────────────
@cms_bp.route("/api/invoices/stats")
@login_required
def invoice_stats():
    base = Invoice.query.filter(Invoice.is_deleted == False)
    return jsonify(
        {
            "draft": base.filter(Invoice.status == "draft").count(),
            "sent": base.filter(Invoice.status == "sent").count(),
            "paid": base.filter(Invoice.status == "paid").count(),
            "overdue": base.filter(Invoice.status == "overdue").count(),
            "cancelled": base.filter(Invoice.status == "cancelled").count(),
        }
    )
