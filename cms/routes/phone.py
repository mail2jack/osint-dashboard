import logging
import re
import copy
import base64
import concurrent.futures
from datetime import datetime, timezone

from cms.services.http_utils import jittered_get

import flask
from flask import request, jsonify, current_app
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Setting
from ..validation import validate, PhoneLookupSchema
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from ..auth import apply_tenant_filter
from ..feature_flags import tool_enabled

logger = logging.getLogger(__name__)


def _validate_phone_input(data):
    phone = data["phone"].strip()
    result = {
        "phone": phone,
        "valid": False,
        "formatted": None,
        "country": None,
        "country_code": None,
        "region": None,
        "carrier": None,
        "line_type": None,
        "timezone": None,
        "normalized": None,
        "services": {},
        "nl_info": None,
    }

    import phonenumbers
    from phonenumbers import geocoder, carrier, timezone as pn_tz

    parsed = phonenumbers.parse(phone, "NL")
    result["valid"] = phonenumbers.is_valid_number(parsed)
    result["formatted"] = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164
    )
    result["country_code"] = f"+{parsed.country_code}"

    try:
        result["country"] = geocoder.description_for_number(parsed, "en")
    except Exception:
        logger.debug("Phone country lookup failed")

    try:
        result["region"] = geocoder.description_for_number(parsed, "nl")
    except Exception:
        logger.debug("Phone region lookup failed")

    try:
        result["carrier"] = carrier.name_for_number(parsed, "nl")
    except Exception:
        logger.debug("Phone carrier lookup failed")

    try:
        ntype = phonenumbers.number_type(parsed)
        line_map = {
            phonenumbers.PhoneNumberType.MOBILE: "Mobile",
            phonenumbers.PhoneNumberType.FIXED_LINE: "Fixed Line",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed Line or Mobile",
            phonenumbers.PhoneNumberType.PAGER: "Pager",
            phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "Personal Number",
            phonenumbers.PhoneNumberType.PREMIUM_RATE: "Premium Rate",
            phonenumbers.PhoneNumberType.SHARED_COST: "Shared Cost",
            phonenumbers.PhoneNumberType.TOLL_FREE: "Toll Free",
            phonenumbers.PhoneNumberType.UAN: "UAN",
            phonenumbers.PhoneNumberType.VOIP: "VoIP",
        }
        result["line_type"] = line_map.get(ntype, str(ntype))
    except Exception:
        logger.debug("Phone line_type lookup failed")

    try:
        tz = pn_tz.time_zones_for_number(parsed)
        result["timezone"] = tz[0] if tz else None
    except Exception:
        logger.debug("Phone timezone lookup failed")

    result["normalized"] = re.sub(r"[^0-9]", "", result["formatted"])
    return result


def _lookup_phone_hlr(phone, result):
    if result.get("country_code") != "+31":
        return
    try:
        bd_url = "https://free.bedrijfsdata.nl/v1.1/phone"
        bd_params = {
            "country_code": "nl",
            "phone": phone.lstrip("+").lstrip("00"),
        }
        bd_resp = jittered_get(bd_url, params=bd_params, timeout=10)
        if bd_resp.status_code == 200:
            bd_data = bd_resp.json().get("phone", {})
            result["nl_info"] = {
                "valid": bd_data.get("valid") == 1,
                "region": bd_data.get("region"),
                "carrier": bd_data.get("carrier"),
                "is_mobile": bd_data.get("ismobile") == 1,
            }
            if bd_data.get("region") and not result.get("region"):
                result["region"] = bd_data["region"]
            if bd_data.get("carrier") and not result.get("carrier"):
                result["carrier"] = bd_data["carrier"]
    except Exception as e:
        logger.debug(f"Phone lookup bedrijfsdata.nl failed ({type(e).__name__}): {e}")


def _lookup_phone_carrier_info(normalized, result, user_id):
    api_key = Setting.get("whatsapp_checkleaked_key")
    if not api_key:
        return

    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    stored_month = Setting.get("whatsapp_checkleaked_month")
    used_count = (
        int(Setting.get("whatsapp_checkleaked_used") or "0")
        if stored_month == now_month
        else 0
    )
    limit = 50
    result["api_usage"] = {
        "used": used_count,
        "limit": limit,
        "remaining": max(0, limit - used_count),
    }
    if used_count >= limit:
        result["api_usage"]["note"] = "Maandlimiet bereikt, gebruik fallback"
        return

    try:
        cl_url = f"https://whatsapp-data1.p.rapidapi.com/number/{normalized}?telegram=1"
        cl_headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "whatsapp-data1.p.rapidapi.com",
        }
        cl_resp = jittered_get(cl_url, headers=cl_headers, timeout=15)
        cl_data = cl_resp.json()
        if "isWAContact" not in cl_data and "isUser" not in cl_data:
            raise Exception(
                f"API returned {cl_resp.status_code}: {cl_data.get('error', 'no data')}"
            )

        wa_exists = cl_data.get("isWAContact") or cl_data.get("isUser")
        tg = cl_data.get("telegram")
        tg_exists = (
            "not on Telegram" not in ((tg or {}).get("error") or "") if tg else None
        )

        profile_pic_b64 = None
        pic_status = cl_data.get("image_status")
        if pic_status and pic_status not in ("item-not-found", "not-authorized"):
            try:
                pic_url = f"https://whatsapp-data1.p.rapidapi.com/picture/{normalized}"
                pic_resp = jittered_get(pic_url, headers=cl_headers, timeout=10)
                if pic_resp.status_code == 200 and pic_resp.headers.get(
                    "content-type", ""
                ).startswith("image/"):
                    profile_pic_b64 = (
                        "data:"
                        + pic_resp.headers["content-type"]
                        + ";base64,"
                        + base64.b64encode(pic_resp.content).decode()
                    )
            except Exception as e:
                logger.debug(
                    f"Phone lookup profile picture fetch failed ({type(e).__name__}): {e}"
                )

        result["services"]["whatsapp"] = {
            "exists": bool(wa_exists),
            "url": f"https://wa.me/{normalized}" if wa_exists else None,
            "business": cl_data.get("isBusiness"),
            "enterprise": cl_data.get("isEnterprise"),
            "verified": cl_data.get("isVerified"),
            "about": cl_data.get("about"),
            "about_set_at": cl_data.get("aboutSetAt"),
            "line_type": cl_data.get("type"),
            "cached": cl_data.get("cached"),
            "check_date": cl_data.get("date"),
            "banned": cl_data.get("checkMetadata", {}).get("isBanned"),
            "image_status": pic_status,
            "profile_picture": profile_pic_b64,
        }
        result["services"]["telegram"] = {
            "exists": tg_exists if tg_exists is not None else None,
            "url": f"https://t.me/+{normalized}" if tg_exists else None,
            "error": (tg or {}).get("error") if tg else None,
        }
        result["raw_api_data"] = cl_data

        from ..models import PhoneLookup

        stored_result = copy.deepcopy(result)
        lookup = PhoneLookup(phone=normalized, created_by=user_id)
        lookup.decrypted_raw_response = stored_result
        lookup.decrypted_profile_picture = profile_pic_b64
        db.session.add(lookup)
        db.session.commit()

        Setting.set("whatsapp_checkleaked_month", now_month)
        Setting.set("whatsapp_checkleaked_used", str(used_count + 1))
        result["api_usage"]["used"] = used_count + 1
        result["api_usage"]["remaining"] = max(0, limit - used_count - 1)
        result["lookup_id"] = lookup.id
    except Exception as e:
        logger.debug(
            f"Phone lookup RapidAPI (whatsapp.checkleaked.cc) failed ({type(e).__name__}): {e}"
        )


def _lookup_phone_online(normalized, result):
    if (
        "whatsapp" in result["services"]
        and result["services"]["whatsapp"].get("exists") is not None
    ):
        return
    try:
        wa_resp = jittered_get(
            f"https://api.whatsapp.com/send?phone={normalized}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        wa_text = wa_resp.text.lower()
        if "phone number is not on whatsapp" in wa_text:
            result["services"]["whatsapp"] = {"exists": False}
        else:
            result["services"]["whatsapp"] = {
                "exists": True,
                "url": f"https://wa.me/{normalized}",
            }
    except Exception as e:
        logger.debug(
            f"Phone lookup WhatsApp fallback scrape failed ({type(e).__name__}): {e}"
        )
        result["services"]["whatsapp"] = {
            "exists": None,
            "note": "Check failed",
        }


def _lookup_phone_social_media(normalized, result):
    if (
        "telegram" in result["services"]
        and result["services"]["telegram"].get("exists") is not None
    ):
        return
    try:
        tg_url = f"https://t.me/+{normalized}"
        tg_resp = jittered_get(
            tg_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        tg_text = tg_resp.text.lower()
        if tg_resp.status_code == 400 or "join" in tg_text or "subscribe" in tg_text:
            result["services"]["telegram"] = {"exists": True, "url": tg_url}
        elif tg_resp.status_code == 200:
            result["services"]["telegram"] = {"exists": False}
        else:
            result["services"]["telegram"] = {
                "exists": None,
                "note": "Unable to verify",
            }
    except Exception as e:
        logger.debug(
            f"Phone lookup Telegram fallback scrape failed ({type(e).__name__}): {e}"
        )
        result["services"]["telegram"] = {
            "exists": None,
            "note": "Check failed",
        }


@cms_bp.route("/api/phone-lookup-stored", methods=["GET", "POST"])
@csrf.exempt
@api_key_required
@login_required
def phone_lookup_stored() -> flask.Response:
    """Return the most recent stored lookup for a phone number."""
    if request.method == "GET":
        phone = (request.args.get("phone") or "").strip()
    else:
        data = request.get_json(silent=True) or {}
        phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"found": False})
    try:
        import phonenumbers

        parsed = phonenumbers.parse(phone, "NL")
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        normalized = re.sub(r"[^0-9]", "", e164)
    except Exception:
        normalized = re.sub(r"[^0-9]", "", phone)
    from ..models import PhoneLookup

    lookup = (
        apply_tenant_filter(
            PhoneLookup.query.filter(
                PhoneLookup.phone == normalized, PhoneLookup.raw_response.isnot(None)
            ),
            PhoneLookup,
        )
        .order_by(PhoneLookup.created_at.desc())
        .first()
    )
    if not lookup:
        return jsonify({"found": False})
    return jsonify(
        {
            "found": True,
            "lookup_id": lookup.id,
            "created_at": lookup.created_at.isoformat() if lookup.created_at else None,
            "raw_response": lookup.decrypted_raw_response,
            "profile_picture": lookup.decrypted_profile_picture,
            "created_by_name": lookup.creator.full_name if lookup.creator else None,
        }
    )


@cms_bp.route("/api/phone-lookup", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("phone")
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="phone")
@validate(PhoneLookupSchema)
def phone_lookup() -> flask.Response:
    data = request.validated_data
    user_id = current_user.id if current_user.is_authenticated else None
    app = current_app._get_current_object()

    def _do_lookup():
        try:
            result = _validate_phone_input(data)
            _lookup_phone_hlr(result["phone"], result)
            _lookup_phone_carrier_info(result["normalized"], result, user_id)
            _lookup_phone_online(result["normalized"], result)
            _lookup_phone_social_media(result["normalized"], result)
        except ImportError:
            return {"error": "phonenumbers library not installed"}
        except Exception:
            logger.exception("Phone lookup error")
            return {"error": "Phone lookup failed"}

        logger.debug(
            f"Phone lookup: {result['phone']} \u2192 valid={result['valid']}, "
            f"carrier={result['carrier']}, region={result['region']}, "
            f"wa={result['services'].get('whatsapp', {}).get('exists')}"
        )
        return result

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

            def _run():
                with app.app_context():
                    return _do_lookup()

            future = pool.submit(_run)
            result = future.result(timeout=30)
    except concurrent.futures.TimeoutError:
        return jsonify({"error": "Phone lookup timed out, try again later"}), 504
    except Exception as e:
        logger.warning(f"Phone lookup failed ({type(e).__name__}): {e}")
        return jsonify({"error": f"Phone lookup failed: {type(e).__name__}"}), 500

    if "error" in result:
        status = 500 if "library not installed" in result["error"] else 500
        return jsonify(result), status
    return jsonify(result), 200
