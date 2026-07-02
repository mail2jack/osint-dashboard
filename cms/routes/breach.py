import logging
from datetime import datetime, timezone

from flask import render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, BreachRecord, AuditLog

logger = logging.getLogger(__name__)


@cms_bp.route("/settings/breaches")
@login_required
def breach_list():
    """Breach notification register — GDPR Articles 33-34."""
    if not current_user.is_super_admin:
        flash("Alleen beheerders hebben toegang.", "danger")
        return redirect(url_for("cms.settings"))

    records = BreachRecord.query.order_by(BreachRecord.detected_at.desc()).all()
    return render_template("cms/settings/breaches.html", records=records)


@cms_bp.route("/api/breaches", methods=["POST"])
@login_required
def breach_create():
    """Log a new data breach."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "Beschrijving is verplicht"}), 400

    detected_at_str = data.get("detected_at")
    detected_at = None
    if detected_at_str:
        try:
            detected_at = datetime.fromisoformat(detected_at_str)
        except (ValueError, TypeError):
            pass
    if detected_at is None:
        detected_at = datetime.now(timezone.utc)

    record = BreachRecord(
        detected_at=detected_at,
        breach_type=(data.get("breach_type") or "").strip(),
        description=description,
        data_affected=(data.get("data_affected") or "").strip(),
        affected_count=data.get("affected_count"),
        risk_level=(data.get("risk_level") or "unknown").strip(),
        remedial_actions=(data.get("remedial_actions") or "").strip(),
    )

    db.session.add(record)
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="breach_record",
        entity_id=record.id,
        description=f"Datalek geregistreerd: {record.breach_type or 'onbekend type'}",
    )
    db.session.commit()

    return jsonify({"status": "ok", "record": record.to_dict()})


@cms_bp.route("/api/breaches/<record_id>", methods=["PUT"])
@login_required
def breach_update(record_id):
    """Update a breach record."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    record = BreachRecord.query.get(record_id)
    if not record:
        return jsonify({"error": "Niet gevonden"}), 404

    data = request.get_json(silent=True) or {}
    if "status" in data:
        record.status = (data["status"] or "open").strip()
    if "breach_type" in data:
        record.breach_type = (data["breach_type"] or "").strip()
    if "description" in data:
        record.description = (data["description"] or "").strip()
    if "data_affected" in data:
        record.data_affected = (data["data_affected"] or "").strip()
    if "affected_count" in data:
        record.affected_count = data.get("affected_count")
    if "risk_level" in data:
        record.risk_level = (data["risk_level"] or "unknown").strip()
    if "remedial_actions" in data:
        record.remedial_actions = (data["remedial_actions"] or "").strip()
    if "authority_notes" in data:
        record.authority_notes = (data["authority_notes"] or "").strip()
    if "subject_communication" in data:
        record.subject_communication = (data["subject_communication"] or "").strip()

    # Art. 33 notification
    if data.get("authority_notified") and not record.authority_notified:
        record.authority_notified = True
        record.authority_notified_at = datetime.now(timezone.utc)
        if data.get("authority_notes"):
            record.authority_notes = data["authority_notes"]

    # Art. 34 notification
    if data.get("subjects_notified") and not record.subjects_notified:
        record.subjects_notified = True
        record.subjects_notified_at = datetime.now(timezone.utc)
        if data.get("subject_communication"):
            record.subject_communication = data["subject_communication"]

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="breach_record",
        entity_id=record.id,
        description=f"Datalek bijgewerkt: {record.breach_type or ''} → status {record.status}",
    )
    db.session.commit()

    return jsonify({"status": "ok", "record": record.to_dict()})


@cms_bp.route("/api/breaches/<record_id>", methods=["DELETE"])
@login_required
def breach_delete(record_id):
    """Delete a breach record."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    record = BreachRecord.query.get(record_id)
    if not record:
        return jsonify({"error": "Niet gevonden"}), 404

    db.session.delete(record)
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="breach_record",
        entity_id=record_id,
        description="Datalek verwijderd uit register",
    )
    db.session.commit()

    return jsonify({"status": "ok"})
