import logging
from urllib.parse import quote

import flask
from flask import request, jsonify
from flask_login import login_required
from curl_cffi.requests import RequestsError

from . import cms_bp
from .. import csrf
from ..api_key_auth import api_key_required
from ..api_keys import _get_overheid_key
from ..feature_flags import tool_enabled
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..validation import validate, OpenKVKQuerySchema
from ..services.http_utils import jittered_get

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/api/kvk-lookup", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("openkvk")
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="kvk")
@validate(OpenKVKQuerySchema)
def kvk_lookup() -> flask.Response:
    """Look up Dutch company data via Overheid.io (KVK)."""
    query = request.validated_data.get("query", "").strip()
    if not query:
        return api_error("Geef een bedrijfsnaam of KVK-nummer", 400)

    api_key = _get_overheid_key()
    if not api_key:
        return jsonify(
            {
                "error": "Overheid.io API key niet geconfigureerd",
                "setup_hint": "Set via Settings > API Keys (overheid_api_key). Gratis key op https://overheid.io",
            }
        ), 400

    try:
        clean_query = quote(query)
        search_url = f"https://api.overheid.io/v3/openkvk?query={clean_query}&size=10"
        headers = {"Accept": "application/json", "ovio-api-key": api_key}

        response = jittered_get(search_url, headers=headers, timeout=15)

        if response.status_code == 403 or response.status_code == 401:
            return api_error("🔑 Auth-fout (ongeldige sleutel)", 400)

        if response.status_code != 200:
            return jsonify(
                {"error": f"Overheid.io API fout: status {response.status_code}"}
            ), 502

        data = response.json()
        bedrijven = data.get("_embedded", {}).get("bedrijf", [])

        if not bedrijven:
            return jsonify({"results": [], "message": "Geen bedrijven gevonden"})

        results = []
        for company in bedrijven:
            href = company.get("_links", {}).get("self", {}).get("href", "")
            if href:
                detail_url = f"https://api.overheid.io{href}"
                try:
                    detail_resp = jittered_get(detail_url, headers=headers, timeout=10)
                    if detail_resp.status_code == 200:
                        detail = detail_resp.json()
                        company.update(detail)
                except RequestsError as e:
                    logger.debug(f"KVK detail fetch failed ({type(e).__name__}): {e}")

            address = company.get("bezoeklocatie") or {}
            result = {
                "kvknummer": company.get("kvknummer"),
                "naam": company.get("naam"),
                "rechtsvorm": company.get("rechtsvormOmschrijving"),
                "rechtsvorm_code": company.get("rechtsvormCode"),
                "actief": company.get("actief"),
                "inschrijvingstype": company.get("inschrijvingstype"),
                "vestigingsnummer": company.get("vestigingsnummer"),
                "straat": address.get("straat"),
                "huisnummer": address.get("huisnummer"),
                "huisnummer_toevoeging": address.get("huisnummerToevoeging"),
                "postcode": address.get("postcode"),
                "plaats": address.get("plaats"),
                "lat": company.get("locatie", {}).get("lat"),
                "lon": company.get("locatie", {}).get("lon"),
                "activiteit": company.get("activiteitomschrijving"),
                "handelsnamen": company.get("huidigeHandelsNamen", []),
                "sbi_codes": [
                    (s.get("sbiCode") if isinstance(s, dict) else s)
                    for s in (company.get("sbi") or [])
                ],
                "slug": company.get("slug"),
            }
            results.append(result)

        return jsonify({"results": results, "count": len(results)})

    except RequestsError as e:
        logger.warning(f"KVK lookup network error: {type(e).__name__}: {e}")
        return jsonify({"error": f"Netwerkfout bij KVK lookup: {e}"}), 502
    except Exception as e:
        logger.exception(f"KVK lookup unexpected error: {e}")
        return jsonify({"error": f"Onverwachte fout: {e}"}), 500
