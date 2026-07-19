import json
import logging
from datetime import datetime, timezone
import uuid

from flask import (
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
)
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Setting, AuditLog
from ..encryption_utils import encryptor

logger = logging.getLogger(__name__)


@cms_bp.route("/privacy")
def privacy_policy():
    """Privacy policy page."""
    return render_template("cms/privacy.html")


@cms_bp.route("/avg/request", methods=["GET", "POST"])
def avg_request():
    """Data Subject Access Request (AVG/DSAR) form."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        request_type = request.form.get("request_type", "access")
        description = request.form.get("description", "").strip()

        if not email:
            flash("Email address is required.", "danger")
            return render_template("cms/avg_request.html")

        if request_type not in (
            "access",
            "rectification",
            "erasure",
            "portability",
            "restrict",
            "object",
        ):
            request_type = "access"

        request_id = str(uuid.uuid4())
        setting_key = f"avg_request_{request_id}"

        Setting.set(
            setting_key,
            {
                "id": request_id,
                "name": name,
                "email": email,
                "phone": phone,
                "request_type": request_type,
                "description": description,
                "status": "received",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            category="avg_requests",
            encrypt=True,
        )

        AuditLog.log(
            action="avg_request_created",
            entity_type="avg_request",
            entity_id=request_id,
            description=f"DSAR received: {request_type} from {name} <{email}>",
        )
        db.session.commit()

        flash(
            "Your DSAR has been received. We will contact you within 30 days.",
            "success",
        )
        return redirect(url_for("cms.privacy_policy"))

    return render_template("cms/avg_request.html")


@cms_bp.route("/api/cookie-consent", methods=["POST"])
def cookie_consent():
    """Store cookie consent preference."""
    data = request.get_json(silent=True) or {}
    consent_level = data.get("consent_level", "necessary")

    if consent_level not in ("necessary", "functional", "analytics", "all"):
        return jsonify({"error": "Invalid consent level"}), 400

    response = jsonify({"status": "ok"})
    max_age = 365 * 24 * 60 * 60
    response.set_cookie(
        "cookie_consent",
        consent_level,
        max_age=max_age,
        httponly=False,
        samesite="Lax",
        secure=request.is_secure,
    )
    response.set_cookie(
        "cookie_consent_set_at",
        datetime.now(timezone.utc).isoformat(),
        max_age=max_age,
        httponly=False,
        samesite="Lax",
        secure=request.is_secure,
    )
    return response


def _deserialize_avg_request(setting: Setting) -> dict | None:
    """Parse a DSAR/AVG request from a Setting row, handling encryption."""
    try:
        raw = setting.value
        if setting.is_encrypted:
            raw = encryptor.decrypt(raw)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw
    except Exception:
        return None


@cms_bp.route("/settings/avg-requests")
@login_required
def avg_requests_admin():
    """Admin page to list and manage DSAR/AVG requests."""
    if not current_user.is_super_admin:
        flash("Only administrators have access.", "danger")
        return redirect(url_for("cms.settings"))

    settings = (
        Setting.query.filter_by(category="avg_requests", is_active=True)
        .order_by(Setting.created_at.desc())
        .all()
    )
    requests = []
    for s in settings:
        data = _deserialize_avg_request(s)
        if data:
            data["_setting_id"] = s.id
            data["_key"] = s.key
            requests.append(data)

    return render_template("cms/settings/avg_requests.html", requests=requests)


@cms_bp.route("/api/avg-requests/<request_id>", methods=["PUT"])
@login_required
def avg_request_update(request_id):
    """Update DSAR request status."""
    if not current_user.is_super_admin:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "").strip()
    if new_status not in ("received", "in_progress", "completed", "rejected"):
        return jsonify({"error": "Invalid status"}), 400

    setting = Setting.query.filter(
        Setting.key == f"avg_request_{request_id}",
        Setting.category == "avg_requests",
    ).first()
    if not setting:
        return jsonify({"error": "Not found"}), 404

    payload = _deserialize_avg_request(setting)
    if not payload:
        return jsonify({"error": "Cannot read request"}), 500

    old_status = payload.get("status")
    payload["status"] = new_status
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    Setting.set(setting.key, payload, category="avg_requests", encrypt=True)

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="avg_request",
        entity_id=request_id,
        description=f"DSAR {request_id}: status '{old_status}' → '{new_status}'",
    )
    db.session.commit()

    return jsonify({"status": "ok", "request": payload})
