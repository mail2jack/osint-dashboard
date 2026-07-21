import logging

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Finding, AuditLog, Case
from ..auth import roles_required
from ..validation import validate, CreateFindingSchema
from ..workflow.research import link_finding_to_manual_action

from .response import api_error
from ..tier_limits import check_resource_limit

logger = logging.getLogger(__name__)


@cms_bp.route("/findings/create", methods=["POST"])
@cms_bp.route("/cases/<case_id>/findings/create", methods=["POST"])
@login_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@validate(CreateFindingSchema)
def create_finding() -> flask.Response:
    """Create a new finding."""
    required = ["case_id", "title", "content"]
    for field in required:
        if not request.validated_data.get(field):
            return api_error(f"{field} is required", 400)

    case = Case.query.filter_by(
        id=request.validated_data["case_id"],
        tenant_id=current_user.tenant_id,
    ).first()
    if not case:
        return api_error("Case not found", 404)

    # Check finding limit before creating
    ok, cur, maximum = check_resource_limit(Finding, "tenant_id", "max_findings")
    if not ok:
        return api_error(
            f"Finding limit reached ({cur}/{maximum}). Upgrade your plan to add more findings.",
            403,
        )

    finding = Finding(
        case_id=request.validated_data["case_id"],
        subject_id=request.validated_data.get("subject_id"),
        title=request.validated_data["title"],
        content=request.validated_data["content"],
        source_url=request.validated_data.get("source_url"),
        source_type=request.validated_data.get("source_type"),
        reliability_score=request.validated_data.get("reliability_score", 5),
        confidence_level=request.validated_data.get("confidence_level"),
        finding_type=request.validated_data.get("finding_type"),
        tags=request.validated_data.get("tags"),
        created_by=current_user.id,
    )

    db.session.add(finding)
    db.session.flush()

    link_finding_to_manual_action(finding.id, finding.case_id, current_user.id)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        case_id=request.validated_data["case_id"],
        description=f"Added finding: {finding.title}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify(
            {"message": "Finding created", "finding": finding.to_dict()}
        ), 201

    return jsonify({"message": "Finding created", "finding": finding.to_dict()})
