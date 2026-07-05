import logging

import flask
from flask import request, jsonify
from flask_login import login_required

from . import cms_bp
from .. import csrf
from ..models import db, Address
from ..validation import validate, PolitiebureauLookupSchema
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from ..auth import ensure_tenant_access
from cms.services.http_utils import jittered_get

from .response import api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/api/politiebureau-lookup", methods=["POST"])
@csrf.exempt
@api_key_required
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="politiebureau")
@validate(PolitiebureauLookupSchema)
def politiebureau_lookup() -> flask.Response:
    """Look up nearest police station for an address."""
    lat = lon = None
    address_info = {}

    address_id = request.validated_data.get("address_id")
    if address_id:
        addr = db.session.get(Address, address_id)
        if not addr:
            return api_error("Address not found", 404)
        ensure_tenant_access(addr)
        addr.decrypt_fields()
        address_info = {
            "street": addr.street,
            "number": addr.number,
            "zipcode": addr.zipcode,
            "town": addr.town,
            "country": addr.country,
        }

        if addr.kadaster_data:
            coords_str = addr.kadaster_data.get("coordinates")
            if coords_str and "POINT(" in coords_str:
                c = coords_str.replace("POINT(", "").replace(")", "").strip().split(" ")
                if len(c) == 2:
                    lon, lat = float(c[0]), float(c[1])
            if (
                not lat
                and addr.kadaster_data.get("lat")
                and addr.kadaster_data.get("lon")
            ):
                lat = float(addr.kadaster_data["lat"])
                lon = float(addr.kadaster_data["lon"])

        if not lat or not lon:
            query = " ".join(
                filter(None, [addr.street, addr.number, addr.zipcode, addr.town])
            )
            if query:
                try:
                    pdok_url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
                    r = jittered_get(
                        pdok_url,
                        params={"q": query, "rows": 1, "fl": "*"},
                        timeout=10,
                    )
                    r.raise_for_status()
                    docs = r.json().get("response", {}).get("docs", [])
                    if docs:
                        cs = docs[0].get("centroide_ll")
                        if cs and "POINT(" in cs:
                            c = (
                                cs.replace("POINT(", "")
                                .replace(")", "")
                                .strip()
                                .split(" ")
                            )
                            if len(c) == 2:
                                lon, lat = float(c[0]), float(c[1])
                except Exception as e:
                    logger.debug(
                        f"Politiebureau lookup geocode (from address_id) failed ({type(e).__name__}): {e}"
                    )

    if not lat or not lon:
        lat = request.validated_data.get("lat")
        lon = request.validated_data.get("lon")

    if not lat or not lon:
        query = request.validated_data.get("query") or ""
        if query:
            try:
                pdok_url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
                r = jittered_get(
                    pdok_url,
                    params={"q": query, "rows": 1, "fl": "*"},
                    timeout=10,
                )
                r.raise_for_status()
                docs = r.json().get("response", {}).get("docs", [])
                if docs and docs[0].get("centroide_ll"):
                    cs = docs[0]["centroide_ll"]
                    if "POINT(" in cs:
                        c = cs.replace("POINT(", "").replace(")", "").strip().split(" ")
                        if len(c) == 2:
                            lon, lat = float(c[0]), float(c[1])
            except Exception as e:
                logger.debug(
                    f"Politiebureau lookup geocode (from query) failed ({type(e).__name__}): {e}"
                )

    if not lat or not lon:
        return jsonify(
            {"error": "Could not determine coordinates for this address"}
        ), 400

    try:
        r = jittered_get(
            "https://api.politie.nl/politiebureaus/v1",
            params={"lat": lat, "lon": lon},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()
        stations = result.get("politiebureaus", [])
        if not stations:
            return jsonify(
                {"found": False, "message": "Geen politiebureaus gevonden in de buurt"}
            ), 200

        s = stations[0]
        addr_bezoek = s.get("bezoekadres", {})
        station_addr = None
        if addr_bezoek.get("adres"):
            station_addr = f"{addr_bezoek['adres']}, {addr_bezoek.get('postcode', '')} {addr_bezoek.get('plaats', '')}"

        return jsonify(
            {
                "found": True,
                "station": {
                    "name": s.get("naam"),
                    "address": station_addr,
                    "phone": s.get("telefoonnummer"),
                    "opening_hours": s.get("openingstijden"),
                    "url": s.get("url"),
                    "location": s.get("locaties", [{}])[0]
                    if s.get("locaties")
                    else None,
                },
                "address": address_info,
                "coordinates": {"lat": lat, "lon": lon},
            }
        ), 200
    except Exception:
        logger.exception("Politiebureau API connection error")
        return jsonify({"error": "Failed to lookup police station"}), 502
