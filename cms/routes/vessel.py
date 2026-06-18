import logging
import json
from datetime import datetime, timezone

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Subject, Finding, AuditLog
from ..auth import roles_required
from ..encryption_utils import encryptor
from ..validation import (
    validate,
    VesselLookupSchema,
    VesselUpdateSubjectSchema,
    VesselFindingSchema,
)
from ..vessel_service import lookup_vessel_async
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from ..feature_flags import tool_enabled

from .response import api_error

logger = logging.getLogger(__name__)

VESSEL_SERVICE_AVAILABLE = True


@cms_bp.route("/api/vessel-lookup", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("vessel")
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="vessel")
@validate(VesselLookupSchema)
def vessel_lookup() -> flask.Response:
    """Look up vessel data from MarinePlan, KVNR, Binnenvaart.eu, Equasis."""
    if not VESSEL_SERVICE_AVAILABLE:
        return jsonify({"error": "Vessel service not available"}), 503

    try:
        data = request.validated_data

        name = (data.get("name") or "").strip()
        imo = (data.get("imo") or "").strip()
        mmsi = (data.get("mmsi") or "").strip()
        eni = (data.get("eni") or "").strip()

        if not name and not imo and not mmsi and not eni:
            return api_error("Provide at least name, IMO, MMSI, or ENI", 400)

        import asyncio

        result = asyncio.run(
            lookup_vessel_async(
                imo=imo or None, mmsi=mmsi or None, eni=eni or None, name=name or None
            )
        )

        subject_id = data.get("subject_id")
        if subject_id and result.get("found"):
            subject = db.session.get(Subject, subject_id)
            if subject:
                result["suggested_update"] = {
                    "imo_number": result.get("imo"),
                    "mmsi": result.get("mmsi"),
                    "eni_number": result.get("eni"),
                    "vessel_nationality": result.get("flag"),
                    "vessel_data": result.get("source_data"),
                }

        return jsonify(result), 200
    except Exception:
        logger.exception("Vessel lookup error")
        return jsonify({"error": "Internal server error"}), 500


@cms_bp.route("/api/vessel/update-subject", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("vessel")
@login_required
@roles_required("admin", "senior_investigator")
@validate(VesselUpdateSubjectSchema)
def update_subject_from_vessel() -> flask.Response:
    """Update subject with vessel data from lookup."""
    data = request.validated_data

    subject = db.session.get(Subject, data["subject_id"])
    if not subject:
        return api_error("Subject not found", 404)
    if subject.subject_type != "vessel":
        return api_error("Subject is not a vessel", 400)

    changes = {}
    vessel_fields = ["imo_number", "mmsi", "eni_number", "vessel_nationality"]
    for field in vessel_fields:
        if data.get(field):
            setattr(subject, field, encryptor.encrypt(str(data[field])))
            changes[field] = {"old": "updated", "new": str(data[field])}

    if data.get("vessel_data"):
        vd = data["vessel_data"]
        if isinstance(vd, str):
            try:
                vd = json.loads(vd)
            except json.JSONDecodeError:
                logger.warning(
                    "vessel_data JSON decode failed, trying ast.literal_eval"
                )
        subject.vessel_data = vd if isinstance(vd, dict) else {}
        changes["vessel_data"] = {"old": "updated", "new": "Vessel data updated"}

    subject.updated_at = datetime.now(timezone.utc)

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="subject",
        entity_id=subject.id,
        changes=changes,
        ip_address=request.remote_addr,
        description=f"Updated vessel subject: {subject.name}",
    )
    db.session.commit()

    return jsonify(
        {"message": "Vessel subject updated", "subject": subject.to_dict()}
    ), 200


@cms_bp.route("/api/findings/from-vessel", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("vessel")
@login_required
@validate(VesselFindingSchema)
def create_finding_from_vessel() -> flask.Response:
    """Create a Finding from vessel lookup data."""
    data = request.validated_data
    case_id = data.get("case_id")
    subject_id = data.get("subject_id")

    if not case_id:
        return api_error("case_id is required", 400)

    vessel_info = data.get("vessel_data", {})
    source = data.get("source", "vessel_lookup")

    if not vessel_info or not isinstance(vessel_info, dict):
        return api_error("vessel_data is required", 400)

    content_parts = ["Vessel Lookup Results", "=" * 30]
    name = vessel_info.get("name") or "Unknown"
    content_parts.append(f"Name: {name}")
    content_parts.append(f"IMO: {vessel_info.get('imo', 'N/A')}")
    content_parts.append(f"MMSI: {vessel_info.get('mmsi', 'N/A')}")
    content_parts.append(f"ENI: {vessel_info.get('eni', 'N/A')}")
    content_parts.append(f"Flag: {vessel_info.get('flag', 'N/A')}")
    content_parts.append(f"Ship Type: {vessel_info.get('ship_type', 'N/A')}")
    content_parts.append(f"Length: {vessel_info.get('length', 'N/A')}")
    content_parts.append(f"Beam: {vessel_info.get('beam', 'N/A')}")
    content_parts.append(f"Year Built: {vessel_info.get('year_built', 'N/A')}")
    content_parts.append(f"Callsign: {vessel_info.get('callsign', 'N/A')}")
    content_parts.append(f"Destination: {vessel_info.get('destination', 'N/A')}")

    pos = vessel_info.get("position")
    if pos:
        content_parts.append(f"Position: {pos.get('lat', '?')}, {pos.get('lon', '?')}")
    if vessel_info.get("speed"):
        content_parts.append(f"Speed: {vessel_info['speed']} km/h")
    if vessel_info.get("builder"):
        content_parts.append(f"Builder: {vessel_info['builder']}")

    sources = vessel_info.get("sources", [])
    content_parts.append(f"\nSources: {', '.join(sources)}")

    sources_data = vessel_info.get("source_data", {})
    if sources_data.get("vesselfinder"):
        content_parts.append(
            f"\nVesselFinder: {sources_data['vesselfinder'].get('source_url', '')}"
        )
    if sources_data.get("marineplan"):
        content_parts.append(
            f"\nMarinePlan: {sources_data['marineplan'].get('source_url', '')}"
        )
    if sources_data.get("kvnr"):
        content_parts.append(f"KVNR: {sources_data['kvnr'].get('source_url', '')}")
    if sources_data.get("binnenvaart"):
        content_parts.append(
            f"Binnenvaart.eu: {sources_data['binnenvaart'].get('source_url', '')}"
        )
    if sources_data.get("equasis"):
        content_parts.append(
            f"Equasis: {sources_data['equasis'].get('source_url', '')}"
        )

    tags = ["vessel", source]
    if vessel_info.get("imo"):
        tags.append(f"imo:{vessel_info['imo']}")

    finding = Finding(
        case_id=case_id,
        subject_id=subject_id,
        title=f"Vessel Check: {name}"[:300],
        content="\n".join(content_parts),
        source_url=data.get("source_url", ""),
        source_type=source,
        finding_type="vessel",
        reliability_score=6,
        confidence_level="medium",
        tags=tags,
        created_by=current_user.id,
    )
    db.session.add(finding)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="finding",
        entity_id=finding.id,
        new_values={"title": finding.title, "source_type": source},
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Created vessel finding: {finding.title}",
    )
    db.session.commit()

    return jsonify(
        {"message": f"Bevinding opgeslagen: {name}", "finding": finding.to_dict()}
    ), 201
