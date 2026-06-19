import logging

import flask
from flask import request, jsonify, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, FinancialRecord, AuditLog, Case
from ..auth import senior_required, case_access_required, apply_tenant_filter
from ..encryption_utils import encryptor
from ..validation import validate, CreateFinancialSchema, VerifyFinancialSchema

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/financials/create", methods=["POST"])
@login_required
@senior_required
@validate(CreateFinancialSchema)
def create_financial() -> flask.Response:
    """Create a new financial record."""
    required = ["case_id", "transaction_date", "amount"]
    for field in required:
        if not request.validated_data.get(field):
            return api_error(f"{field} is required", 400)

    from datetime import date

    raw_date = request.validated_data["transaction_date"]
    try:
        parsed_date = (
            date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
        )
    except (ValueError, TypeError):
        return jsonify(
            {"error": "Invalid transaction_date format. Use YYYY-MM-DD."}
        ), 400

    record = FinancialRecord(
        case_id=request.validated_data["case_id"],
        subject_id=request.validated_data.get("subject_id"),
        transaction_date=parsed_date,
        amount=request.validated_data["amount"],
        currency=request.validated_data.get("currency", "EUR"),
        transaction_type=request.validated_data.get("transaction_type"),
        source=request.validated_data.get("source"),
        source_reference=request.validated_data.get("source_reference"),
        description=request.validated_data.get("description"),
    )

    # Encrypt counterparty details
    encrypted_fields = [
        "counterparty_name",
        "counterparty_account",
        "counterparty_bank",
        "counterparty_country",
    ]
    for field in encrypted_fields:
        if request.validated_data.get(field):
            setattr(record, field, encryptor.encrypt(request.validated_data[field]))

    db.session.add(record)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="financial_record",
        entity_id=record.id,
        ip_address=request.remote_addr,
        case_id=request.validated_data["case_id"],
        new_values={"amount": str(record.amount), "date": str(record.transaction_date)},
        description=f"Added financial record: {record.amount} {record.currency}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify(
            {"message": "Financial record created", "record": record.to_dict()}
        ), 201

    flash("Financial record added.", "success")
    return redirect(url_for("cms.view_case", case_id=request.validated_data["case_id"]))


@cms_bp.route("/financials/<record_id>/verify", methods=["POST"])
@csrf.exempt
@login_required
@senior_required
@validate(VerifyFinancialSchema)
def verify_financial(record_id: str) -> flask.Response:
    """Verify or flag a financial record."""
    record = db.session.get(FinancialRecord, record_id) or abort(404)

    action = request.validated_data.get("action")  # 'verify' or 'flag'
    notes = request.validated_data.get("notes", "")

    if action == "verify":
        record.verify(current_user.id, notes)
        action_type = "verify"
    else:
        record.flag(current_user.id, notes)
        action_type = "flag"

    AuditLog.log(
        user_id=current_user.id,
        action=action_type,
        entity_type="financial_record",
        entity_id=record_id,
        ip_address=request.remote_addr,
        case_id=record.case_id,
        description=f"{action_type.capitalize()}d financial record",
    )
    db.session.commit()

    return jsonify({"message": f"Record {action_type}ed", "record": record.to_dict()})


# =============================================================================
# Financial Summary Routes
# =============================================================================


@cms_bp.route("/cases/<case_id>/financial-summary")
@login_required
@case_access_required
def get_financial_summary(case_id: str) -> flask.Response:
    """Get aggregated financial data for a case."""
    db.session.get(Case, case_id) or abort(404)
    records = apply_tenant_filter(
        FinancialRecord.query.filter_by(case_id=case_id, is_deleted=False),
        FinancialRecord,
    ).all()

    if not records:
        return jsonify(
            {
                "summary": {
                    "total_records": 0,
                    "total_amount": 0,
                    "currency": "EUR",
                    "by_type": {},
                    "by_status": {},
                    "by_source": {},
                    "by_month": {},
                    "top_counterparties": [],
                }
            }
        )

    # Calculate totals
    total_amount = sum(float(r.amount or 0) for r in records)

    # Group by transaction type
    by_type = {}
    for r in records:
        t = r.transaction_type or "unknown"
        if t not in by_type:
            by_type[t] = {"count": 0, "total": 0}
        by_type[t]["count"] += 1
        by_type[t]["total"] += float(r.amount or 0)

    # Group by verification status
    by_status = {}
    for r in records:
        s = r.verification_status or "pending"
        if s not in by_status:
            by_status[s] = {"count": 0, "total": 0}
        by_status[s]["count"] += 1
        by_status[s]["total"] += float(r.amount or 0)

    # Group by source
    by_source = {}
    for r in records:
        s = r.source or "unknown"
        if s not in by_source:
            by_source[s] = {"count": 0, "total": 0}
        by_source[s]["count"] += 1
        by_source[s]["total"] += float(r.amount or 0)

    # Group by month
    by_month = {}
    for r in records:
        if r.transaction_date:
            month_key = r.transaction_date.strftime("%Y-%m")
            if month_key not in by_month:
                by_month[month_key] = {"count": 0, "total": 0}
            by_month[month_key]["count"] += 1
            by_month[month_key]["total"] += float(r.amount or 0)

    # Top counterparties
    counterparties = {}
    for r in records:
        name = r.counterparty_name or "Unknown"
        if name not in counterparties:
            counterparties[name] = 0
        counterparties[name] += float(r.amount or 0)

    top_counterparties = sorted(
        [{"name": k, "total": v} for k, v in counterparties.items()],
        key=lambda x: x["total"],
        reverse=True,
    )[:10]

    return jsonify(
        {
            "summary": {
                "total_records": len(records),
                "total_amount": round(total_amount, 2),
                "currency": records[0].currency if records else "EUR",
                "by_type": by_type,
                "by_status": by_status,
                "by_source": by_source,
                "by_month": by_month,
                "top_counterparties": top_counterparties,
            }
        }
    )
