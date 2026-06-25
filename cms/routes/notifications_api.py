import logging

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Notification

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/api/notifications")
@login_required
def list_notifications() -> flask.Response:
    """Get notifications for current user."""
    limit = request.args.get("limit", 20, type=int)
    offset = request.args.get("offset", 0, type=int)
    all_items = request.args.get("all", "").lower() in ("1", "true")
    category = request.args.get("category", "").strip()

    q = Notification.query.filter_by(user_id=current_user.id)
    if not all_items:
        q = q.filter_by(is_read=False)
    if category:
        q = q.filter_by(category=category)
    total = q.count()
    q = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit)

    return jsonify(
        {
            "notifications": [n.to_dict() for n in q.all()],
            "total": total,
            "unread_count": Notification.query.filter_by(
                user_id=current_user.id, is_read=False
            ).count(),
        }
    )


@cms_bp.route("/api/notifications/<notif_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notif_id: str) -> flask.Response:
    """Mark a single notification as read."""
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if not notif:
        return api_error("Notification not found", 404)
    notif.is_read = True
    db.session.commit()
    return jsonify({"status": "ok"})


@cms_bp.route("/api/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read() -> flask.Response:
    """Mark all notifications as read for current user."""
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
        {"is_read": True}
    )
    db.session.commit()
    return jsonify({"status": "ok"})


@cms_bp.route("/api/notifications/<notif_id>", methods=["DELETE"])
@login_required
def delete_notification(notif_id: str) -> flask.Response:
    """Delete a single notification."""
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if not notif:
        return api_error("Notification not found", 404)
    db.session.delete(notif)
    db.session.commit()
    return jsonify({"status": "ok"})


@cms_bp.route("/api/notifications", methods=["DELETE"])
@login_required
def delete_all_read_notifications() -> flask.Response:
    """Delete all read notifications for current user."""
    q = Notification.query.filter_by(user_id=current_user.id, is_read=True)
    count = q.count()
    q.delete(synchronize_session="fetch")
    db.session.commit()
    return jsonify({"status": "ok", "deleted": count})


@cms_bp.route("/api/notifications/preferences", methods=["GET"])
@login_required
def get_notification_preferences() -> flask.Response:
    """Get notification preferences for current user."""
    from ..models import NotificationPreference, NOTIFICATION_CATEGORIES

    prefs = NotificationPreference.query.filter_by(user_id=current_user.id).all()
    pref_map = {p.category: p for p in prefs}

    result = []
    for cat_key, cat_label in NOTIFICATION_CATEGORIES:
        p = pref_map.get(cat_key)
        result.append(
            {
                "category": cat_key,
                "label": cat_label,
                "web_enabled": p.web_enabled if p else True,
                "email_enabled": p.email_enabled if p else False,
                "sms_enabled": p.sms_enabled if p else False,
                "whatsapp_enabled": p.whatsapp_enabled if p else False,
            }
        )
    return jsonify({"preferences": result})


@cms_bp.route("/api/notifications/preferences", methods=["PUT"])
@login_required
def update_notification_preferences() -> flask.Response:
    """Update notification preferences for current user."""
    from ..models import NotificationPreference

    data = request.get_json(silent=True) or {}
    prefs_data = data.get("preferences", [])
    for item in prefs_data:
        cat = item.get("category", "").strip()
        if not cat:
            continue
        pref = NotificationPreference.query.filter_by(
            user_id=current_user.id, category=cat
        ).first()
        if not pref:
            pref = NotificationPreference(user_id=current_user.id, category=cat)
            db.session.add(pref)
        if "web_enabled" in item:
            pref.web_enabled = bool(item["web_enabled"])
        if "email_enabled" in item:
            pref.email_enabled = bool(item["email_enabled"])
        if "sms_enabled" in item:
            pref.sms_enabled = bool(item["sms_enabled"])
        if "whatsapp_enabled" in item:
            pref.whatsapp_enabled = bool(item["whatsapp_enabled"])
    db.session.commit()
    return jsonify({"status": "ok"})
