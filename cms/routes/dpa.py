import logging

from flask import render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, DpaRecord, AuditLog

logger = logging.getLogger(__name__)


@cms_bp.route("/settings/dpa")
@login_required
def dpa_register():
    """DPA register page — list all sub-processor records."""
    if not current_user.is_super_admin:
        flash("Only administrators have access to the DPA register.", "danger")
        return redirect(url_for("cms.settings"))

    records = DpaRecord.query.order_by(DpaRecord.name).all()
    return render_template("cms/settings/dpa.html", records=records)


@cms_bp.route("/api/dpa", methods=["POST"])
@login_required
def dpa_create():
    """Create a new DPA record."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    record = DpaRecord(
        name=name,
        purpose=(data.get("purpose") or "").strip(),
        data_categories=(data.get("data_categories") or "").strip(),
        country=(data.get("country") or "").strip(),
        transfer_safeguard=(data.get("transfer_safeguard") or "").strip(),
        status=(data.get("status") or "active").strip(),
        notes=(data.get("notes") or "").strip(),
    )
    contract_date = data.get("contract_date")
    if contract_date:
        from datetime import date

        try:
            record.contract_date = date.fromisoformat(contract_date)
        except (ValueError, TypeError):
            pass

    db.session.add(record)
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="dpa_record",
        entity_id=record.id,
        description=f"DPA register: processor '{name}' added",
    )
    db.session.commit()

    return jsonify({"status": "ok", "record": record.to_dict()})


@cms_bp.route("/api/dpa/<record_id>", methods=["PUT"])
@login_required
def dpa_update(record_id):
    """Update an existing DPA record."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    record = DpaRecord.query.get(record_id)
    if not record:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(silent=True) or {}
    old_name = record.name

    name = (data.get("name") or "").strip()
    if name:
        record.name = name
    if "purpose" in data:
        record.purpose = (data["purpose"] or "").strip()
    if "data_categories" in data:
        record.data_categories = (data["data_categories"] or "").strip()
    if "country" in data:
        record.country = (data["country"] or "").strip()
    if "transfer_safeguard" in data:
        record.transfer_safeguard = (data["transfer_safeguard"] or "").strip()
    if "status" in data:
        record.status = (data["status"] or "active").strip()
    if "notes" in data:
        record.notes = (data["notes"] or "").strip()
    if "contract_date" in data:
        from datetime import date

        val = data["contract_date"]
        try:
            record.contract_date = date.fromisoformat(val) if val else None
        except (ValueError, TypeError):
            pass

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="dpa_record",
        entity_id=record.id,
        description=f"DPA register: processor '{old_name}' updated",
    )
    db.session.commit()

    return jsonify({"status": "ok", "record": record.to_dict()})


@cms_bp.route("/api/dpa/<record_id>", methods=["DELETE"])
@login_required
def dpa_delete(record_id):
    """Delete a DPA record."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    record = DpaRecord.query.get(record_id)
    if not record:
        return jsonify({"error": "Not found"}), 404

    name = record.name
    db.session.delete(record)
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="dpa_record",
        entity_id=record_id,
        description=f"DPA register: processor '{name}' deleted",
    )
    db.session.commit()

    return jsonify({"status": "ok"})
