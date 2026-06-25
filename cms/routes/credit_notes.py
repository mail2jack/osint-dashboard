"""
Credit note routes — CRUD, PDF, issue from invoice.
"""

import json
import logging
from datetime import date

import flask
from flask import request, render_template, abort, redirect, url_for, flash
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db,
    CreditNote,
    CreditNoteItem,
    Invoice,
    InvoiceItem,
    Client,
    AuditLog,
)
from ..auth import (
    admin_required,
    senior_required,
    apply_tenant_filter,
    ensure_tenant_access,
)

logger = logging.getLogger(__name__)


def _cn_to_dict(cn: CreditNote) -> dict:
    invoice = db.session.get(Invoice, cn.invoice_id) if cn.invoice_id else None
    creator = cn.creator
    client_name = ""
    if invoice:
        client = db.session.get(Client, invoice.client_id)
        if client:
            client_name = client.name
    return {
        "id": cn.id,
        "credit_note_number": cn.credit_note_number,
        "invoice_number": invoice.invoice_number if invoice else None,
        "invoice_id": cn.invoice_id,
        "client_name": client_name,
        "issue_date": cn.issue_date.isoformat() if cn.issue_date else None,
        "reason": cn.reason,
        "status": cn.status,
        "currency": cn.currency,
        "subtotal": float(cn.subtotal),
        "vat_amount": float(cn.vat_amount),
        "total": float(cn.total),
        "created_by": creator.full_name if creator else None,
        "created_at": cn.created_at.isoformat() if cn.created_at else None,
    }


# ── List ────────────────────────────────────────────────────────────────
@cms_bp.route("/credit-notes")
@login_required
def credit_note_list():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)
    search = request.args.get("q", "").strip()

    q = apply_tenant_filter(CreditNote.query, CreditNote)
    if search:
        like = f"%{search}%"
        q = q.filter(CreditNote.credit_note_number.ilike(like))
    q = q.order_by(CreditNote.created_at.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    notes = [_cn_to_dict(cn) for cn in pagination.items]
    total_issued = sum(
        float(cn.total)
        for cn in apply_tenant_filter(
            CreditNote.query.filter(CreditNote.status == "issued"), CreditNote
        ).all()
    )

    return render_template(
        "cms/invoicing/credit_note_list.html",
        credit_notes=notes,
        pagination=pagination,
        search=search,
        total_issued=total_issued,
    )


# ── Create from invoice ─────────────────────────────────────────────────
@cms_bp.route("/invoices/<invoice_id>/create-credit-note", methods=["GET", "POST"])
@login_required
@senior_required
def credit_note_create(invoice_id: str):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    ensure_tenant_access(invoice)
    if invoice.status == "cancelled":
        flash("Cannot create a credit note for a cancelled invoice.", "error")
        return redirect(url_for("cms.invoice_view", invoice_id=invoice_id))

    inv_items = (
        InvoiceItem.query.filter_by(invoice_id=invoice_id)
        .order_by(InvoiceItem.sort_order)
        .all()
    )

    if request.method == "GET":
        return render_template(
            "cms/invoicing/credit_note_create.html",
            invoice=invoice,
            inv_items=inv_items,
        )

    reason = request.form.get("reason", "").strip()
    items_json = request.form.get("items", "[]")
    try:
        items_data = json.loads(items_json)
    except (json.JSONDecodeError, TypeError):
        items_data = []

    if not items_data:
        flash("Select at least one item to credit.", "error")
        return redirect(url_for("cms.credit_note_create", invoice_id=invoice_id))

    cn = CreditNote(
        credit_note_number=CreditNote.generate_number(),
        invoice_id=invoice_id,
        issue_date=date.today(),
        reason=reason or None,
        created_by=current_user.id,
    )
    db.session.add(cn)
    db.session.flush()

    for i, item in enumerate(items_data):
        cn_item = CreditNoteItem(
            credit_note_id=cn.id,
            invoice_item_id=item.get("invoice_item_id"),
            description=item.get("description", ""),
            quantity=float(item.get("quantity", 1)),
            unit_price=float(item.get("unit_price", 0)),
            vat_rate=float(item.get("vat_rate", 21.00)),
            sort_order=i,
        )
        cn_item.recalculate()
        db.session.add(cn_item)

    cn.recalculate()
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="credit_note",
        entity_id=cn.id,
        ip_address=request.remote_addr,
        description=f"Created credit note {cn.credit_note_number} for invoice {invoice.invoice_number}",
    )
    db.session.commit()
    flash(f"Credit note {cn.credit_note_number} created.", "success")
    return redirect(url_for("cms.credit_note_view", credit_note_id=cn.id))


# ── View ─────────────────────────────────────────────────────────────────
@cms_bp.route("/credit-notes/<credit_note_id>")
@login_required
def credit_note_view(credit_note_id: str):
    cn = db.session.get(CreditNote, credit_note_id) or abort(404)
    ensure_tenant_access(cn)
    invoice = db.session.get(Invoice, cn.invoice_id) if cn.invoice_id else None
    if invoice:
        ensure_tenant_access(invoice)
    client = None
    if invoice:
        client = db.session.get(Client, invoice.client_id)
        if client:
            ensure_tenant_access(client)
    items = (
        CreditNoteItem.query.filter_by(credit_note_id=credit_note_id)
        .order_by(CreditNoteItem.sort_order)
        .all()
    )

    return render_template(
        "cms/invoicing/credit_note_view.html",
        cn=cn,
        invoice=invoice,
        client=client,
        items=items,
    )


# ── Issue ─────────────────────────────────────────────────────────────────
@cms_bp.route("/credit-notes/<credit_note_id>/issue", methods=["POST"])
@login_required
@senior_required
def credit_note_issue(credit_note_id: str):
    cn = db.session.get(CreditNote, credit_note_id) or abort(404)
    ensure_tenant_access(cn)
    if cn.status != "draft":
        flash("Credit note is already issued.", "info")
        return redirect(url_for("cms.credit_note_view", credit_note_id=credit_note_id))
    cn.mark_issued()
    AuditLog.log(
        user_id=current_user.id,
        action="issue",
        entity_type="credit_note",
        entity_id=cn.id,
        ip_address=request.remote_addr,
        description=f"Issued credit note {cn.credit_note_number}",
    )
    db.session.commit()
    flash(f"Credit note {cn.credit_note_number} issued.", "success")
    return redirect(url_for("cms.credit_note_view", credit_note_id=credit_note_id))


# ── PDF ───────────────────────────────────────────────────────────────────
@cms_bp.route("/credit-notes/<credit_note_id>/pdf")
@login_required
def credit_note_pdf(credit_note_id: str):
    cn = db.session.get(CreditNote, credit_note_id) or abort(404)
    ensure_tenant_access(cn)
    from ..services.invoice_service import generate_credit_note_pdf

    try:
        pdf_data = generate_credit_note_pdf(cn)
    except Exception as e:
        logger.error("PDF generation failed for credit note %s: %s", credit_note_id, e)
        flash("PDF generation failed.", "error")
        return redirect(url_for("cms.credit_note_view", credit_note_id=credit_note_id))

    safe_num = cn.credit_note_number.replace("/", "-")
    return flask.Response(
        pdf_data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={safe_num}.pdf"},
    )


# ── Delete ───────────────────────────────────────────────────────────────
@cms_bp.route("/credit-notes/<credit_note_id>/delete", methods=["POST"])
@login_required
@admin_required
def credit_note_delete(credit_note_id: str):
    cn = db.session.get(CreditNote, credit_note_id) or abort(404)
    ensure_tenant_access(cn)
    db.session.delete(cn)
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="credit_note",
        entity_id=cn.id,
        ip_address=request.remote_addr,
        description=f"Deleted credit note {cn.credit_note_number}",
    )
    db.session.commit()
    flash(f"Credit note {cn.credit_note_number} deleted.", "success")
    return redirect(url_for("cms.credit_note_list"))
