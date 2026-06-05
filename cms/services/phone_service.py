import logging
import re
from flask import request, jsonify
import flask

from curl_cffi import requests as curl_requests
from curl_cffi import CurlError
from cms.services.http_utils import jitter_sleep
from search_history import search_history

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

WHATSAPP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_phone_number(phone: str) -> str:
    if not phone:
        return phone
    cleaned = re.sub(r"[^\d+]", "", phone)
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("00"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("0"):
        cleaned = cleaned[1:]
    return cleaned


def phone_osint() -> flask.Response:
    """Comprehensive phone number OSINT lookup"""
    import phonenumbers
    from phonenumbers import geocoder, carrier, timezone

    data = request.get_json()
    phone = data.get("phone", "")

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

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
        "is_valid": False,
        "services": {},
    }

    try:
        parsed = phonenumbers.parse(phone, None)
        result["valid"] = phonenumbers.is_valid_number(parsed)
        result["formatted"] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )

        try:
            country = geocoder.description_for_number(parsed, "en")
            result["country"] = country
        except Exception:
            logger.debug("Phone enrichment: country lookup failed")

        try:
            result["country_code"] = f"+{parsed.country_code}"
        except Exception:
            logger.debug("Phone enrichment: country_code lookup failed")

        try:
            region = geocoder.description_for_number(parsed, None)
            result["region"] = region
        except Exception:
            logger.debug("Phone enrichment: region lookup failed")

        try:
            carrier_name = carrier.name_for_number(parsed, "en")
            result["carrier"] = carrier_name
        except Exception:
            logger.debug("Phone enrichment: carrier lookup failed")

        try:
            line_type = carrier._api_for_number(parsed).get("type", "unknown")
            if callable(line_type):
                line_type = line_type(parsed)
            result["line_type"] = str(line_type)
        except Exception:
            logger.debug("Phone enrichment: line_type lookup failed")

        try:
            tz = timezone.time_zones_for_number(parsed)
            result["timezone"] = tz[0] if tz else None
        except Exception:
            logger.debug("Phone enrichment: timezone lookup failed")

        normalized = normalize_phone_number(phone)
        result["normalized"] = normalized

        try:
            jitter_sleep(domain_hint="https://api.whatsapp.com")
            wa_response = curl_requests.get(
                f"https://api.whatsapp.com/send?phone={normalized}",
                headers=WHATSAPP_HEADERS,
                impersonate="chrome124",
                timeout=10,
            )
            wa_text = wa_response.text.lower()
            if "phone number is not on whatsapp" in wa_text:
                result["services"]["whatsapp"] = {
                    "exists": False,
                    "url": f"https://wa.me/{normalized}",
                }
            elif "unavailable" in wa_text or "cannot send" in wa_text:
                result["services"]["whatsapp"] = {
                    "exists": None,
                    "note": "API unavailable",
                }
            else:
                result["services"]["whatsapp"] = {
                    "exists": True,
                    "url": f"https://wa.me/{normalized}",
                }
        except Exception as e:
            logger.debug(f"WhatsApp forced check failed ({type(e).__name__}): {e}")
            result["services"]["whatsapp"] = {
                "exists": None,
                "note": "Check blocked",
            }

        try:
            tg_url = f"https://t.me/+{normalized}"
            jitter_sleep(domain_hint="https://t.me")
            tg_response = curl_requests.get(
                tg_url,
                headers=HEADERS,
                impersonate="chrome124",
                timeout=5,
            )
            tg_text = tg_response.text.lower()
            if (
                tg_response.status_code == 400
                or "join" in tg_text
                or "subscribe" in tg_text
            ):
                result["services"]["telegram"] = {"exists": True, "url": tg_url}
            elif tg_response.status_code == 200:
                result["services"]["telegram"] = {"exists": False}
            else:
                result["services"]["telegram"] = {
                    "exists": None,
                    "note": "Unable to verify",
                }
        except Exception as e:
            logger.debug(f"Telegram forced check failed ({type(e).__name__}): {e}")
            result["services"]["telegram"] = {
                "exists": None,
                "note": "Check blocked",
            }

        _tck, _tcn = _get_twochat_credentials()
        if _tck and _tcn:
            try:
                phone_e164 = result.get("formatted") or f"+{normalized}"
                url = f"https://api.p.2chat.io/open/whatsapp/check-number/{_tcn}/{phone_e164}"
                headers = {"X-User-API-Key": _tck, "Accept": "application/json"}
                response = curl_requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    result["services"]["whatsapp_2chat"] = {
                        "exists": data.get("on_whatsapp"),
                        "is_business": data.get("whatsapp_info", {}).get("is_business"),
                        "verified_level": data.get("whatsapp_info", {}).get(
                            "verified_level"
                        ),
                        "status_text": data.get("whatsapp_info", {}).get("status_text"),
                        "profile_pic": data.get("whatsapp_info", {}).get(
                            "contact_profile_pic"
                        ),
                        "number_id": data.get("whatsapp_info", {}).get("number_id"),
                        "region": data.get("number", {}).get("region"),
                        "timezone": data.get("number", {}).get("timezone", []),
                    }
                    biz_info = data.get("whatsapp_info", {}).get(
                        "business_information", {}
                    )
                    if biz_info:
                        result["services"]["whatsapp_2chat"]["business"] = {
                            "name": biz_info.get("verified_name"),
                            "description": biz_info.get("description"),
                            "website": biz_info.get("website", []),
                        }
            except Exception as e:
                logger.warning(f"2Chat WhatsApp check failed ({type(e).__name__}): {e}")

        search_history.add_entry(
            "phone",
            phone,
            f"Valid: {result['valid']}, Country: {result['country']}, Carrier: {result['carrier']}",
            1 if result["valid"] else 0,
        )

        return jsonify(result)

    except phonenumbers.NumberParseException:
        logger.exception("Phone number parse error")
        return jsonify({"error": "Invalid phone number format"}), 400
    except Exception:
        logger.exception("Phone OSINT error")
        return jsonify({"error": "Internal server error"}), 500


def whatsapp_lookup() -> flask.Response:
    """Check if a phone number exists on WhatsApp"""
    data = request.get_json()
    phone = data.get("phone", "")

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    normalized = normalize_phone_number(phone)

    if len(normalized) < 10:
        return jsonify({"error": "Invalid phone number format"}), 400

    result = {
        "phone": normalized,
        "query": phone,
        "exists": None,
        "status": "checking",
        "url": f"https://wa.me/{normalized}",
    }

    try:
        url = f"https://api.whatsapp.com/send?phone={normalized}"

        jitter_sleep(domain_hint="https://api.whatsapp.com")
        response = curl_requests.get(
            url, headers=WHATSAPP_HEADERS, impersonate="chrome124", timeout=10
        )
        text = response.text.lower()

        result["http_status"] = response.status_code

        absence_patterns = [
            "phone number is not on whatsapp",
            "is unavailable",
            "cannot send messages to this number",
            "invalid phone number",
            "check the number",
        ]

        has_absence = any(pattern in text for pattern in absence_patterns)

        if has_absence:
            result["exists"] = False
            result["status"] = "not_found"
            result["message"] = "Phone number not found on WhatsApp"
        else:
            result["exists"] = True
            result["status"] = "found"
            result["message"] = "Phone number found on WhatsApp"

    except CurlError as e:
        if "timed out" in str(e).lower():
            result["status"] = "timeout"
            result["message"] = "Request timed out"
        else:
            result["status"] = "connection_error"
            result["message"] = "Connection error"
    except Exception:
        logger.exception("WhatsApp dedicated lookup error")
        result["status"] = "error"
        result["message"] = "Lookup failed"

    search_history.add_entry(
        "whatsapp", phone, result["message"], 1 if result["exists"] else 0
    )

    return jsonify(result)


def check_whatsapp_2chat() -> flask.Response:
    """Check if a phone number is on WhatsApp using 2Chat API.
    Requires TWOCHAT_API_KEY and TWOCHAT_WHATSAPP_NUMBER environment variables."""
    data = request.get_json()
    phone = data.get("phone", "")

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    _tck, _tcn = _get_twochat_credentials()
    if not _tck or not _tcn:
        return jsonify(
            {
                "error": "2Chat API not configured",
                "setup_required": True,
                "instructions": {
                    "api_key": "Set via Settings > API Keys (twochat_api_key) or TWOCHAT_API_KEY env var",
                    "whatsapp_number": "Set via Settings > API Keys (twochat_whatsapp_number) or TWOCHAT_WHATSAPP_NUMBER env var",
                    "docs": "https://developers.2chat.co/docs/API/WhatsApp/Web/check-number",
                },
            }
        ), 400

    normalized = normalize_phone_number(phone)

    if len(normalized) < 10:
        return jsonify({"error": "Invalid phone number format"}), 400

    result = {
        "phone": normalized,
        "query": phone,
        "on_whatsapp": None,
        "number_id": None,
        "is_business": None,
        "verified_level": None,
        "status_text": None,
        "profile_pic": None,
        "region": None,
        "timezone": None,
        "source": "2chat",
    }

    try:
        url = f"https://api.p.2chat.io/open/whatsapp/check-number/{_tcn}/{normalized}"

        headers = {"X-User-API-Key": _tck, "Accept": "application/json"}

        response = curl_requests.get(url, headers=headers, timeout=30)
        data = response.json()

        if response.status_code == 200:
            result["on_whatsapp"] = data.get("on_whatsapp", False)
            result["is_valid"] = data.get("is_valid", False)

            number_info = data.get("number", {})
            result["region"] = number_info.get("region")
            result["timezone"] = number_info.get("timezone", [])

            whatsapp_info = data.get("whatsapp_info", {})
            if whatsapp_info:
                result["number_id"] = whatsapp_info.get("number_id")
                result["is_business"] = whatsapp_info.get("is_business")
                result["verified_level"] = whatsapp_info.get("verified_level")
                result["status_text"] = whatsapp_info.get("status_text")
                result["profile_pic"] = whatsapp_info.get("contact_profile_pic")
                result["pushname"] = whatsapp_info.get("pushname")

                biz_info = whatsapp_info.get("business_information", {})
                if biz_info:
                    result["business"] = {
                        "name": biz_info.get("verified_name"),
                        "short_name": biz_info.get("short_name"),
                        "description": biz_info.get("description"),
                        "website": biz_info.get("website", []),
                        "email": biz_info.get("email"),
                        "currency": biz_info.get("currency"),
                    }

            result["message"] = (
                "Found on WhatsApp"
                if result["on_whatsapp"]
                else "Not found on WhatsApp"
            )
        else:
            result["error"] = data.get("message", "API request failed")
            result["http_status"] = response.status_code

    except CurlError:
        result["error"] = "Request failed"
    except Exception:
        logger.exception("2Chat API dedicated error")
        result["error"] = "API request failed"

    search_history.add_entry(
        "phone_2chat",
        phone,
        result.get("message", "Error"),
        1 if result.get("on_whatsapp") else 0,
    )

    return jsonify(result)


def telegram_lookup() -> flask.Response:
    """Check if a phone number exists on Telegram"""
    data = request.get_json()
    phone = data.get("phone", "")

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    normalized = normalize_phone_number(phone)

    if len(normalized) < 10:
        return jsonify({"error": "Invalid phone number format"}), 400

    result = {
        "phone": normalized,
        "query": phone,
        "exists": None,
        "status": "checking",
        "url": f"https://t.me/+{normalized}",
    }

    try:
        url = f"https://t.me/+{normalized}"

        jitter_sleep(domain_hint="https://t.me")
        response = curl_requests.get(
            url, headers=HEADERS, impersonate="chrome124", timeout=10
        )
        result["http_status"] = response.status_code
        text = response.text.lower()

        if response.status_code == 400:
            result["exists"] = False
            result["status"] = "not_found"
            result["message"] = "Invalid Telegram link or number not found"
        elif response.status_code == 200:
            if "telegram" in text and (
                "join" in text or "subscribe" in text or "confirm" in text
            ):
                result["exists"] = True
                result["status"] = "found"
                result["message"] = "Phone number linked to Telegram"
            else:
                result["exists"] = None
                result["status"] = "unknown"
                result["message"] = "Unable to determine Telegram status"
        else:
            result["exists"] = None
            result["status"] = "unknown"
            result["message"] = f"Status code: {response.status_code}"

    except CurlError as e:
        if "timed out" in str(e).lower():
            result["status"] = "timeout"
            result["message"] = "Request timed out"
        else:
            result["status"] = "connection_error"
            result["message"] = "Connection error"
    except Exception:
        logger.exception("Telegram dedicated lookup error")
        result["status"] = "error"
        result["message"] = "Lookup failed"

    search_history.add_entry(
        "telegram", phone, result["message"], 1 if result["exists"] else 0
    )

    return jsonify(result)


def carrier_lookup() -> flask.Response:
    """Get carrier and validation information for a phone number"""
    import phonenumbers
    from phonenumbers import carrier, geocoder, NumberParseException

    data = request.get_json()
    phone = data.get("phone", "")

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    normalized = normalize_phone_number(phone)

    if len(normalized) < 10:
        return jsonify({"error": "Invalid phone number format"}), 400

    result = {
        "phone": normalized,
        "query": phone,
        "carrier": None,
        "line_type": None,
        "country": None,
        "country_code": None,
        "valid": None,
        "status": "checking",
        "message": None,
    }

    try:
        phone_to_parse = phone
        if not phone_to_parse.startswith("+"):
            phone_to_parse = "+" + phone_to_parse

        parsed = phonenumbers.parse(phone_to_parse, None)
        result["valid"] = phonenumbers.is_valid_number(parsed)
        result["possible"] = phonenumbers.is_possible_number(parsed)

        if result["possible"]:
            result["country_code"] = f"+{parsed.country_code}"

            try:
                location = geocoder.description_for_number(parsed, "en")
                if location:
                    result["country"] = location
            except Exception:
                logger.debug("Phone location lookup failed")

            try:
                carrier_name = carrier.name_for_number(parsed, "en")
                if carrier_name:
                    result["carrier"] = carrier_name
            except Exception:
                logger.debug("Phone carrier lookup failed")

            number_type = phonenumbers.number_type(parsed)
            type_map = {
                phonenumbers.PhoneNumberType.MOBILE: "Mobile",
                phonenumbers.PhoneNumberType.FIXED_LINE: "Fixed Line",
                phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed Line or Mobile",
                phonenumbers.PhoneNumberType.PAGER: "Pager",
                phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "Personal Number",
                phonenumbers.PhoneNumberType.PREMIUM_RATE: "Premium Rate",
                phonenumbers.PhoneNumberType.SHARED_COST: "Shared Cost",
                phonenumbers.PhoneNumberType.TOLL_FREE: "Toll Free",
                phonenumbers.PhoneNumberType.UAN: "UAN",
                phonenumbers.PhoneNumberType.UNKNOWN: "Unknown",
                phonenumbers.PhoneNumberType.VOICEMAIL: "Voicemail",
                phonenumbers.PhoneNumberType.VOIP: "VoIP",
            }
            result["line_type"] = type_map.get(number_type, "Unknown")

            if result["valid"]:
                result["status"] = "found"
                result["message"] = (
                    f"Valid {result['line_type']} number from {result['country'] or 'Unknown'}"
                )
            else:
                result["status"] = "not_found"
                result["message"] = "Number is not valid for any region"
        else:
            result["status"] = "not_found"
            result["message"] = "Number format not possible"

    except NumberParseException:
        logger.exception("Phone number parse error")
        result["status"] = "error"
        result["message"] = "Failed to parse number"
    except Exception:
        logger.exception("Carrier lookup error")
        result["status"] = "error"
        result["message"] = "Lookup failed"

    search_history.add_entry(
        "carrier",
        phone,
        f"{result.get('carrier', 'Unknown')} - {result.get('line_type', 'Unknown')}",
        1 if result.get("valid") else 0,
    )

    return jsonify(result)


def phone_lookup_all() -> flask.Response:
    """Check phone number on multiple services"""
    data = request.get_json()
    phone = data.get("phone", "")
    services = data.get("services", ["whatsapp", "telegram", "carrier"])

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    results = {}

    if "whatsapp" in services:
        whatsapp_result = whatsapp_lookup()
        results["whatsapp"] = whatsapp_result.get_json()

    if "telegram" in services:
        telegram_result = telegram_lookup()
        results["telegram"] = telegram_result.get_json()

    if "carrier" in services:
        carrier_result = carrier_lookup()
        results["carrier"] = carrier_result.get_json()

    return jsonify(
        {"phone": normalize_phone_number(phone), "query": phone, "results": results}
    )


def _get_twochat_credentials() -> tuple[str, str]:
    """Get 2Chat credentials from Settings."""
    from cms.app_helpers import _get_twochat_credentials as _gtc

    return _gtc()
