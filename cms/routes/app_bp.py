import logging
import os
from datetime import datetime

from flask import request, jsonify, Response as FlaskResponse, send_file
from flask_login import login_required

from .app_blueprint import app_routes_bp
from ..app_helpers import generate_results_pdf
from cms.validation import validate, GeneratePDFSchema
from .response import api_error

logger = logging.getLogger(__name__)

# Import sub-modules to register routes on app_routes_bp
from . import ai_routes  # noqa: F401
from . import osint_routes  # noqa: F401
from . import history_routes  # noqa: F401


# =============================================================================
# PDF Routes
# =============================================================================


@app_routes_bp.route("/api/generate-pdf", methods=["POST"])
@login_required
@validate(GeneratePDFSchema)
def generate_pdf() -> FlaskResponse:
    results = request.validated_data.get("results", {})
    search_type = request.validated_data.get("type", "unknown")
    query = request.validated_data.get("query", "unknown")

    try:
        from flask_login import current_user

        viewer_info = (
            f"{current_user.full_name or current_user.username} — {current_user.role}"
            if current_user.is_authenticated
            else "Anonymous"
        )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        watermark = f"Exported by {viewer_info} on {ts} | OSINT Dashboard"
        filename = generate_results_pdf(
            results, search_type, query, watermark_text=watermark
        )
        return jsonify(
            {
                "success": True,
                "filename": filename,
                "download_url": f"/download/{os.path.basename(filename)}",
            }
        )
    except Exception:
        logger.exception("PDF generation error")
        return jsonify({"error": "Internal server error"}), 500


@app_routes_bp.route("/download/<filename>")
@login_required
def download_pdf(filename) -> FlaskResponse:
    safe_filename = os.path.basename(filename)
    path = os.path.join("reports", safe_filename)
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name=safe_filename)
    return api_error("File not found", 404)


# =============================================================================
# Phone Routes (added via add_url_rule)
# =============================================================================

from cms.services.phone_service import (
    phone_osint,
    whatsapp_lookup,
    check_whatsapp_2chat,
    telegram_lookup,
    carrier_lookup,
    phone_lookup_all,
)
from cms.validation import validate, PhoneNumberSchema, PhoneLookupAllSchema
from cms.rate_limiting import rate_limit, STRICT_RATE_LIMIT

# Apply auth, validation + rate limiting
phone_osint = rate_limit(limit=STRICT_RATE_LIMIT, key_prefix="phone")(phone_osint)
phone_osint = validate(PhoneNumberSchema)(phone_osint)
phone_osint = login_required(phone_osint)

whatsapp_lookup = validate(PhoneNumberSchema)(whatsapp_lookup)
whatsapp_lookup = login_required(whatsapp_lookup)

check_whatsapp_2chat = validate(PhoneNumberSchema)(check_whatsapp_2chat)
check_whatsapp_2chat = login_required(check_whatsapp_2chat)

telegram_lookup = validate(PhoneNumberSchema)(telegram_lookup)
telegram_lookup = login_required(telegram_lookup)

carrier_lookup = validate(PhoneNumberSchema)(carrier_lookup)
carrier_lookup = login_required(carrier_lookup)

phone_lookup_all = validate(PhoneLookupAllSchema)(phone_lookup_all)
phone_lookup_all = login_required(phone_lookup_all)

# Register routes
app_routes_bp.add_url_rule("/api/phone", "phone_osint", phone_osint, methods=["POST"])
app_routes_bp.add_url_rule(
    "/api/whatsapp", "whatsapp_lookup", whatsapp_lookup, methods=["POST"]
)
app_routes_bp.add_url_rule(
    "/api/phone/2chat",
    "check_whatsapp_2chat",
    check_whatsapp_2chat,
    methods=["POST"],
)
app_routes_bp.add_url_rule(
    "/api/telegram", "telegram_lookup", telegram_lookup, methods=["POST"]
)
app_routes_bp.add_url_rule(
    "/api/carrier", "carrier_lookup", carrier_lookup, methods=["POST"]
)
app_routes_bp.add_url_rule(
    "/api/phone-lookup",
    "phone_lookup_all",
    phone_lookup_all,
    methods=["POST"],
)
