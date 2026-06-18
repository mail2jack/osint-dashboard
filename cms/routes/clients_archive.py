import logging

import flask
from flask import request, jsonify, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Client, Case, AuditLog
from ..auth import roles_required

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/clients/<client_id>/archive", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
def archive_client(client_id: str) -> flask.Response:
    """Archive a client if no active cases exist."""
    client = db.session.get(Client, client_id) or abort(404)

    if not client.is_active:
        return api_error("Client is already archived", 400)

    # Check if client has any non-closed/non-archived cases
    active_cases = Case.query.filter(
        Case.client_id == client_id,
        Case.is_deleted == False,
        Case.status.in_(["open", "active", "suspended"]),
    ).count()
    if active_cases > 0:
        return jsonify(
            {
                "error": f"Kan niet archiveren: client heeft {active_cases} actieve za(a)k(en)"
            }
        ), 400

    client.is_active = False

    AuditLog.log(
        user_id=current_user.id,
        action="archive",
        entity_type="client",
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Archived client: {client.name}",
    )
    db.session.commit()

    flash(f"Client {client.name} is gearchiveerd.", "info")

    if request.is_json:
        return jsonify({"success": True, "message": "Client archived"})
    return redirect(url_for("cms.clients"))


@cms_bp.route("/clients/<client_id>/restore", methods=["POST"])
@login_required
@roles_required("admin", "senior_investigator")
def restore_client(client_id: str) -> flask.Response:
    """Restore an archived client."""
    client = db.session.get(Client, client_id) or abort(404)

    if client.is_active:
        return api_error("Client is already active", 400)

    client.is_active = True

    AuditLog.log(
        user_id=current_user.id,
        action="restore",
        entity_type="client",
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Restored client: {client.name}",
    )
    db.session.commit()

    flash(f"Client {client.name} is hersteld.", "info")

    if request.is_json:
        return jsonify({"success": True, "message": "Client restored"})
    return redirect(url_for("cms.view_client", client_id=client.id))
