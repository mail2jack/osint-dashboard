"""Notification history and preferences page routes."""

import logging

from flask import render_template
from flask_login import login_required

from . import cms_bp

logger = logging.getLogger(__name__)


@cms_bp.route("/notifications")
@login_required
def notification_history():
    """Full notification history page."""
    return render_template("cms/notifications.html")


@cms_bp.route("/notifications/preferences")
@login_required
def notification_preferences():
    """Notification preferences page."""
    return render_template("cms/notifications_preferences.html")
