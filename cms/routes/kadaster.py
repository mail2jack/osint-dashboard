import logging

import flask
from flask import request, jsonify
from flask_login import login_required

from . import cms_bp
from .. import csrf
from ..models import db, Address
from ..validation import validate, KadasterLookupSchema
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from ..feature_flags import tool_enabled
from curl_cffi import requests as curl_requests
from cms.services.http_utils import jitter_sleep

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/api/kadaster-lookup", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("kadaster")
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="kadaster")
@validate(KadasterLookupSchema)
def kadaster_lookup() -> flask.Response:
    """Look up a Dutch address in the BAG via PDOK API."""
    data = request.validated_data

    # Resolve address_id from DB if provided
    address_id = data.get("address_id")
    if address_id and not data.get("street") and not data.get("zipcode"):
        addr = db.session.get(Address, address_id)
        if addr:
            addr.decrypt_fields()
            data["street"] = addr.street
            data["number"] = addr.number
            data["zipcode"] = addr.zipcode
            data["town"] = addr.town

    query = data.get("query", "")
    if not query:
        parts = []
        if data.get("street"):
            parts.append(data["street"])
        if data.get("number"):
            parts.append(data["number"])
        if data.get("zipcode"):
            parts.append(data["zipcode"])
        if data.get("town"):
            parts.append(data["town"])
        query = " ".join(parts)

    if not query:
        return api_error("No address provided", 400)

    try:
        pdok_url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
        params = {"q": query, "rows": 1, "fl": "*"}

        jitter_sleep(domain_hint=pdok_url)
        resp = curl_requests.get(
            pdok_url, params=params, timeout=10, impersonate="chrome124"
        )
        resp.raise_for_status()
        result = resp.json()

        docs = result.get("response", {}).get("docs", [])
        if not docs:
            logger.warning(f"Kadaster lookup not found: {query}")
            return jsonify(
                {
                    "found": False,
                    "message": "Address not found in BAG registry",
                    "query": query,
                }
            ), 200

        doc = docs[0]
        logger.info(
            f"Kadaster lookup OK: {query} -> {doc.get('straatnaam')} {doc.get('huisnummer')}, {doc.get('postcode')} {doc.get('woonplaatsnaam')}"
        )
        return jsonify(
            {
                "found": True,
                "query": query,
                "bag_data": {
                    "street": doc.get("straatnaam"),
                    "number": doc.get("huisnummer"),
                    "number_letter": doc.get("huisletter"),
                    "number_addition": doc.get("huisnummertoevoeging"),
                    "zipcode": doc.get("postcode"),
                    "town": doc.get("woonplaatsnaam"),
                    "municipality": doc.get("gemeentenaam"),
                    "province": doc.get("provincienaam"),
                    "coordinates": doc.get("centroide_ll"),
                    "purpose": doc.get("gebruiksdoel"),
                    "surface": doc.get("oppervlakte"),
                    "building_year": doc.get("bouwjaar"),
                    "bag_id": doc.get("bag_id"),
                    "status": doc.get("status"),
                    "type": doc.get("type"),
                },
            }
        ), 200
    except Exception:
        logger.exception("Kadaster API connection error")
        return jsonify({"error": "Failed to lookup address"}), 502
