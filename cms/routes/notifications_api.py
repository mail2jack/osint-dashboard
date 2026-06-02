import logging

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Notification

logger = logging.getLogger(__name__)


@cms_bp.route("/api/notifications")
@login_required
def list_notifications() -> flask.Response:
    """Get unread notifications for current user."""
    limit = request.args.get("limit", 20, type=int)
    all_items = request.args.get("all", "").lower() == "1"

    q = Notification.query.filter_by(user_id=current_user.id)
    if not all_items:
        q = q.filter_by(is_read=False)
    q = q.order_by(Notification.created_at.desc()).limit(limit)

    return jsonify(
        {
            "notifications": [
                {
                    "id": n.id,
                    "message": n.message,
                    "link": n.link,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in q.all()
            ],
            "unread_count": Notification.query.filter_by(
                user_id=current_user.id, is_read=False
            ).count(),
        }
    )


@cms_bp.route("/api/notifications/<notif_id>/read", methods=["POST"])
@csrf.exempt
@login_required
def mark_notification_read(notif_id: str) -> flask.Response:
    """Mark a single notification as read."""
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if not notif:
        return jsonify({"error": "Notification not found"}), 404
    notif.is_read = True
    db.session.commit()
    return jsonify({"status": "ok"})


@cms_bp.route("/api/notifications/read-all", methods=["POST"])
@csrf.exempt
@login_required
def mark_all_notifications_read() -> flask.Response:
    """Mark all notifications as read for current user."""
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
        {"is_read": True}
    )
    db.session.commit()
    return jsonify({"status": "ok"})
