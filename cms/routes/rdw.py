import logging
from curl_cffi import requests as curl_requests
from cms.services.http_utils import jitter_sleep

import flask
from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Subject, Comment, AuditLog
from ..auth import roles_required, ensure_tenant_access
from ..validation import validate, RDWCheckSchema, RDWUpdateSchema
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from ..feature_flags import tool_enabled

from .response import api_error

logger = logging.getLogger(__name__)

RDW_API_BASE = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"


def _normalize_kenteken(kenteken: str) -> str:
    return kenteken.upper().replace("-", "").replace(" ", "")


def _denormalize_kenteken(kenteken: str) -> str:
    kenteken = kenteken.upper().replace("-", "").replace(" ", "")
    if len(kenteken) == 6:
        return f"{kenteken[:2]}-{kenteken[2:5]}-{kenteken[5:]}"
    elif len(kenteken) == 5:
        return f"{kenteken[:2]}-{kenteken[2:4]}-{kenteken[4:]}"
    return kenteken


@cms_bp.route("/check-rdw-vehicle", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("rdw")
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="rdw")
@validate(RDWCheckSchema)
def check_rdw_vehicle() -> flask.Response:
    """Check vehicle data from RDW (Dutch Road Transport Authority)."""
    data = request.validated_data
    kenteken = data["kenteken"].strip()
    subject_id = data.get("subject_id")

    if not kenteken:
        return api_error("Kenteken (license plate) is required", 400)

    kenteken_normalized = _normalize_kenteken(kenteken)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; OSINT-CMS/1.0)",
            "Accept": "application/json",
        }
        url = f"{RDW_API_BASE}?kenteken={kenteken_normalized}"
        jitter_sleep(domain_hint=url)
        r = curl_requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            return jsonify(
                {
                    "error": f"RDW API returned status {r.status_code}",
                    "kenteken": kenteken_normalized,
                }
            ), 502

        results = r.json()
        if not results:
            return jsonify(
                {
                    "found": False,
                    "kenteken": kenteken_normalized,
                    "kenteken_display": _denormalize_kenteken(kenteken_normalized),
                    "message": "No vehicle found for this license plate",
                }
            ), 200

        vehicle = results[0]
        vehicle_data = {
            "found": True,
            "kenteken": vehicle.get("kenteken", ""),
            "kenteken_display": _denormalize_kenteken(vehicle.get("kenteken", "")),
            "voertuigsoort": vehicle.get("voertuigsoort", ""),
            "merk": vehicle.get("merk", ""),
            "handelsbenaming": vehicle.get("handelsbenaming", ""),
            "inrichting": vehicle.get("inrichting", ""),
            "type": vehicle.get("type", ""),
            "variant": vehicle.get("variant", ""),
            "uitvoering": vehicle.get("uitvoering", ""),
            "kleur": vehicle.get("eerste_kleur", ""),
            "tweede_kleur": vehicle.get("tweede_kleur", ""),
            "aantal_deuren": vehicle.get("aantal_deuren", ""),
            "aantal_zitplaatsen": vehicle.get("aantal_zitplaatsen", ""),
            "cilinderinhoud": vehicle.get("cilinderinhoud", ""),
            "aantal_cilinders": vehicle.get("aantal_cilinders", ""),
            "vermogen": vehicle.get("vermogen_massarijklaar", ""),
            "massa_ledig": vehicle.get("massa_ledig_voertuig", ""),
            "maximum_massa": vehicle.get("toegestane_maximum_massa_voertuig", ""),
            "wielbasis": vehicle.get("wielbasis", ""),
            "datum_eerste_toelating": vehicle.get("datum_eerste_toelating", ""),
            "datum_tenaamstelling": vehicle.get("datum_tenaamstelling", ""),
            "vervaldatum_apk": vehicle.get("vervaldatum_apk", ""),
            "europese_voertuigcategorie": vehicle.get("europese_voertuigcategorie", ""),
            "wam_verzekerd": vehicle.get("wam_verzekerd", ""),
            "taxi_indicator": vehicle.get("taxi_indicator", ""),
            "export_indicator": vehicle.get("export_indicator", ""),
            "zuinigheidsclassificatie": vehicle.get("zuinigheidsclassificatie", ""),
            "catalogusprijs": vehicle.get("catalogusprijs", ""),
            "bruto_bpm": vehicle.get("bruto_bpm", ""),
            "openstaande_terugroepactie": vehicle.get(
                "openstaande_terugroepactie_indicator", ""
            ),
            "typegoedkeuringsnummer": vehicle.get("typegoedkeuringsnummer", ""),
        }

        if subject_id:
            vehicle_data["subject_id"] = subject_id
            vehicle_data["suggested_update"] = {
                "brand": vehicle.get("merk", ""),
                "vehicle_type": vehicle.get("inrichting", ""),
                "notes": f"RDW Data: {vehicle.get('merk', '')} {vehicle.get('handelsbenaming', '')} ({_denormalize_kenteken(vehicle.get('kenteken', ''))})",
            }

        return jsonify(vehicle_data), 200
    except curl_requests.RequestsError:
        logger.exception("RDW API connection error")
        return jsonify({"error": "Failed to connect to RDW API"}), 503
    except Exception:
        logger.exception("RDW check error")
        return jsonify({"error": "Failed to check RDW data"}), 500


@cms_bp.route("/subjects/<subject_id>/update-from-rdw", methods=["POST"])
@csrf.exempt
@api_key_required
@login_required
@roles_required("admin", "senior_investigator")
@validate(RDWUpdateSchema)
def update_subject_from_rdw(subject_id: str) -> flask.Response:
    """Update vehicle subject fields with data from RDW."""
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return api_error("Subject not found", 404)
    ensure_tenant_access(subject)
    if subject.subject_type != "vehicle":
        return api_error("Subject is not a vehicle", 400)

    data = request.validated_data

    kenteken = _normalize_kenteken(data.get("kenteken"))

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; OSINT-CMS/1.0)",
            "Accept": "application/json",
        }
        url = f"{RDW_API_BASE}?kenteken={kenteken}"
        jitter_sleep(domain_hint=url)
        r = curl_requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200 or not r.json():
            return api_error("Vehicle not found in RDW database", 404)

        vehicle = r.json()[0]

        if vehicle.get("merk"):
            subject.brand = vehicle.get("merk")
        if vehicle.get("inrichting"):
            subject.vehicle_type = vehicle.get("inrichting")

        rdw_notes = [f"Kenteken: {_denormalize_kenteken(kenteken)}"]
        if vehicle.get("merk"):
            rdw_notes.append(f"Merk: {vehicle.get('merk')}")
        if vehicle.get("handelsbenaming"):
            rdw_notes.append(f"Model: {vehicle.get('handelsbenaming')}")
        if vehicle.get("voertuigsoort"):
            rdw_notes.append(f"Type: {vehicle.get('voertuigsoort')}")
        if vehicle.get("inrichting"):
            rdw_notes.append(f"Inrichting: {vehicle.get('inrichting')}")
        if vehicle.get("kleur"):
            rdw_notes.append(f"Kleur: {vehicle.get('eerste_kleur')}")
        if vehicle.get("vervaldatum_apk"):
            rdw_notes.append(f"APK vervaldatum: {vehicle.get('vervaldatum_apk')}")
        if vehicle.get("wam_verzekerd"):
            rdw_notes.append(f"Verzekerd (WAM): {vehicle.get('wam_verzekerd')}")

        rdw_comment = Comment(
            subject_id=subject.id,
            content="[RDW Data]\n" + "\n".join(rdw_notes),
            comment_type="note",
            author_id=current_user.id,
        )
        db.session.add(rdw_comment)

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="subject",
            entity_id=subject_id,
            ip_address=request.remote_addr,
            description=f"Updated vehicle data from RDW for: {_denormalize_kenteken(kenteken)}",
        )
        db.session.commit()

        return jsonify(
            {"message": "Subject updated from RDW data", "subject": subject.to_dict()}
        ), 200
    except Exception:
        logger.exception("RDW update error")
        db.session.rollback()
        return jsonify({"error": "Internal server error"}), 500
