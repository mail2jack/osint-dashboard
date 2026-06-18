from .response import api_error

"""
Bulk CSV import for Subjects and Clients.
"""

import csv
import io
import logging

import flask
from flask import request, jsonify
from flask_login import login_required

from . import cms_bp
from ..models import db, Subject, Client
from ..auth import senior_required
from .. import csrf

logger = logging.getLogger(__name__)

MAX_IMPORT_ROWS = 500


@cms_bp.route("/api/subjects/import-csv", methods=["POST"])
@csrf.exempt
@login_required
@senior_required
def import_subjects_csv() -> flask.Response:
    """Bulk-import subjects from CSV upload."""
    if "file" not in request.files:
        return api_error("No file provided", 400)

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".csv"):
        return api_error("File must be a CSV", 400)

    try:
        stream = io.StringIO(f.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)
        rows = list(reader)
    except Exception:
        return api_error("Could not parse CSV file", 400)

    if not rows:
        return jsonify({"created": 0, "errors": ["CSV file is empty"]}), 400

    if len(rows) > MAX_IMPORT_ROWS:
        return jsonify(
            {"error": f"Maximum {MAX_IMPORT_ROWS} records per import, got {len(rows)}"}
        ), 400

    created = 0
    errors = []

    for i, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        subject_type = (row.get("subject_type") or "").strip()
        if not name or not subject_type:
            errors.append(f"Row {i}: name and subject_type are required")
            continue

        try:
            subject = Subject(
                name=name,
                subject_type=subject_type,
                email=row.get("email") or None,
                phone=row.get("phone") or None,
                risk_score=int(row.get("risk_score", 0))
                if row.get("risk_score")
                else 0,
                notes=row.get("notes") or None,
            )
            subject.encrypt_identifiers()
            db.session.add(subject)
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")
            continue

    db.session.commit()

    return jsonify({"created": created, "errors": errors})


@cms_bp.route("/api/clients/import-csv", methods=["POST"])
@csrf.exempt
@login_required
@senior_required
def import_clients_csv() -> flask.Response:
    """Bulk-import clients from CSV upload."""
    if "file" not in request.files:
        return api_error("No file provided", 400)

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".csv"):
        return api_error("File must be a CSV", 400)

    try:
        stream = io.StringIO(f.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)
        rows = list(reader)
    except Exception:
        return api_error("Could not parse CSV file", 400)

    if not rows:
        return jsonify({"created": 0, "errors": ["CSV file is empty"]}), 400

    if len(rows) > MAX_IMPORT_ROWS:
        return jsonify(
            {"error": f"Maximum {MAX_IMPORT_ROWS} records per import, got {len(rows)}"}
        ), 400

    created = 0
    errors = []

    for i, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: name is required")
            continue

        try:
            client = Client(
                name=name,
                is_company=row.get("is_company", "").lower() in ("1", "true", "yes")
                if row.get("is_company")
                else False,
                contract_number=row.get("contract_number") or None,
            )
            if row.get("contact_person"):
                client.contact_person = row["contact_person"]
            if row.get("contact_email"):
                client.contact_email = row["contact_email"]
            if row.get("contact_phone"):
                client.contact_phone = row["contact_phone"]
            client.encrypt_naw()
            db.session.add(client)
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")
            continue

    db.session.commit()

    return jsonify({"created": created, "errors": errors})
