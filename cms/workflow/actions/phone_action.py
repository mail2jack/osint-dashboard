import logging

from cms.services.http_utils import jittered_get
from cms.services.phone_service import (
    _whatsapp_check_internal,
    _telegram_check_internal,
)
from cms.workflow.actions.helpers import _action_subject

logger = logging.getLogger(__name__)


def _phone_check(action):
    subject = _action_subject(action)
    phone = action.data_value if action.data_value else None
    if not phone:
        phone = subject.phone if subject else None
    if not phone:
        return []
    findings = []
    subject_id = subject.id if subject else None

    import phonenumbers
    from phonenumbers import geocoder, carrier as pn_carrier, timezone as pn_tz

    detail_parts = []
    enrichment = {}

    try:
        parsed = phonenumbers.parse(phone, "NL")
        valid = phonenumbers.is_valid_number(parsed)
        enrichment["valid"] = valid
        detail_parts.append(f"Valid: {'Yes' if valid else 'No'}")

        try:
            region = geocoder.description_for_number(parsed, "nl")
            if region:
                enrichment["region"] = region
                detail_parts.append(f"Region: {region}")
        except Exception:
            logger.debug("Geocoder lookup failed for %s", phone, exc_info=True)

        try:
            carrier_name = pn_carrier.name_for_number(parsed, "nl")
            if carrier_name:
                enrichment["carrier"] = carrier_name
                detail_parts.append(f"Carrier: {carrier_name}")
        except Exception:
            logger.debug("Carrier lookup failed for %s", phone, exc_info=True)

        try:
            line_type = pn_carrier._api_for_number(parsed).get("type", "unknown")
            if callable(line_type):
                line_type = line_type(parsed)
            enrichment["line_type"] = str(line_type)
            type_map = {
                0: "Landline",
                1: "Mobile",
                2: "VoIP",
                3: "Personal number",
                5: "Voicemail",
                7: "Satellite",
            }
            label = (
                type_map.get(int(line_type))
                if str(line_type).isdigit()
                else str(line_type)
            )
            detail_parts.append(f"Type: {label or line_type}")
        except Exception:
            logger.debug("Line type lookup failed for %s", phone, exc_info=True)

        try:
            tz = pn_tz.time_zones_for_number(parsed)
            if tz:
                enrichment["timezone"] = tz[0]
                detail_parts.append(f"Timezone: {tz[0]}")
        except Exception:
            logger.debug("Timezone lookup failed for %s", phone, exc_info=True)

    except phonenumbers.NumberParseException:
        logger.debug("Failed to parse phone number %s", phone, exc_info=True)

    e164 = (
        f"+{parsed.country_code}{parsed.national_number}"
        if "parsed" in dir()
        else phone
    )

    findings.append(
        {
            "title": f"Phone number {phone} — {enrichment.get('valid', True) and 'Valid' or 'Invalid'}",
            "detail": "\n".join(detail_parts)
            if detail_parts
            else f"Phone number: {phone}",
            "source_type": "phone",
            "icon": "📞",
            "verified": bool(enrichment.get("valid")),
            "subject_id": subject_id,
        }
    )

    wa = _whatsapp_check_internal(phone)
    if wa.get("exists") is True:
        findings.append(
            {
                "title": "WhatsApp account found",
                "detail": "This number is active on WhatsApp.",
                "source_url": wa.get("url"),
                "source_type": "phone",
                "icon": "💬",
                "verified": False,
                "subject_id": subject_id,
            }
        )
    elif wa.get("exists") is False:
        findings.append(
            {
                "title": "No WhatsApp account",
                "detail": "This number was not found on WhatsApp.",
                "source_type": "phone",
                "icon": "💬",
                "verified": False,
                "subject_id": subject_id,
            }
        )

    tg = _telegram_check_internal(phone)
    if tg.get("exists") is True:
        findings.append(
            {
                "title": "Telegram account found",
                "detail": "This number is active on Telegram.",
                "source_url": tg.get("url"),
                "source_type": "phone",
                "icon": "✈️",
                "verified": False,
                "subject_id": subject_id,
            }
        )

    from cms.services.phone_service import _whatsapp_check_baileys

    ba = _whatsapp_check_baileys(e164)
    if ba.get("on_whatsapp") is True and not ba.get("error"):
        detail_lines = []
        detail_lines.append("Status: Active on WhatsApp")
        if ba.get("is_business"):
            detail_lines.append("Type: Business account")
        else:
            detail_lines.append("Type: Personal account")
        if ba.get("status_text"):
            detail_lines.append(f"Status text: {ba['status_text']}")
        biz = ba.get("business") or {}
        if biz.get("description"):
            detail_lines.append(f"Description: {biz['description']}")
        if biz.get("website"):
            detail_lines.append(f"Website: {', '.join(biz['website'])}")
        if biz.get("email"):
            detail_lines.append(f"Email: {biz['email']}")
        if biz.get("category"):
            detail_lines.append(f"Category: {biz['category']}")
        if biz.get("address"):
            detail_lines.append(f"Address: {biz['address']}")
        if ba.get("profile_pic"):
            detail_lines.append(f"Profile photo: {ba['profile_pic']}")
        if detail_lines:
            findings.append(
                {
                    "title": "WhatsApp Business data"
                    if ba.get("is_business")
                    else "WhatsApp data",
                    "detail": "\n".join(detail_lines),
                    "source_type": "phone",
                    "icon": "🏢" if ba.get("is_business") else "💬",
                    "verified": True,
                    "subject_id": subject_id,
                    "screenshots": [
                        {
                            "url": ba["profile_pic"],
                            "source_url": None,
                        }
                    ]
                    if ba.get("profile_pic")
                    else [],
                }
            )
    elif ba.get("error"):
        # fall back to 2Chat if available
        try:
            from cms.services.phone_service import _get_twochat_credentials

            api_key, channel_id = _get_twochat_credentials()
            if api_key and channel_id:
                twochat_url = f"https://api.p.2chat.io/open/whatsapp/check-number/{channel_id}/{e164}"
                twochat_headers = {
                    "X-User-API-Key": api_key,
                    "Accept": "application/json",
                }
                twochat_resp = jittered_get(
                    twochat_url, headers=twochat_headers, timeout=30
                )
                if twochat_resp.status_code == 200:
                    tc_data = twochat_resp.json()
                    on_wa = tc_data.get("on_whatsapp")
                    wa_info = tc_data.get("whatsapp_info", {}) or {}
                    biz_info = wa_info.get("business_information", {}) or {}
                    detail_lines = []
                    if on_wa is True:
                        detail_lines.append(
                            "Status: Active on WhatsApp (via 2Chat API)"
                        )
                    elif on_wa is False:
                        detail_lines.append("Status: Not active on WhatsApp")
                    else:
                        detail_lines.append("Status: Unknown")
                    if wa_info.get("verified_level"):
                        detail_lines.append(
                            f"Verified level: {wa_info['verified_level']}"
                        )
                    if wa_info.get("status_text"):
                        detail_lines.append(f"Status text: {wa_info['status_text']}")
                    if wa_info.get("number_id"):
                        detail_lines.append(f"Number ID: {wa_info['number_id']}")
                    region_info = tc_data.get("number", {})
                    if region_info.get("region"):
                        detail_lines.append(f"Region (2Chat): {region_info['region']}")
                    if region_info.get("timezone"):
                        detail_lines.append(
                            f"Timezone (2Chat): {', '.join(region_info['timezone'])}"
                        )
                    if biz_info.get("verified_name"):
                        detail_lines.append(
                            f"Business name: {biz_info['verified_name']}"
                        )
                    if biz_info.get("description"):
                        detail_lines.append(f"Description: {biz_info['description']}")
                    if biz_info.get("website"):
                        detail_lines.append(
                            f"Website: {', '.join(biz_info['website'])}"
                        )
                    if wa_info.get("contact_profile_pic"):
                        detail_lines.append(
                            f"Profile photo: {wa_info['contact_profile_pic']}"
                        )
                    if detail_lines:
                        findings.append(
                            {
                                "title": "WhatsApp Business API data",
                                "detail": "\n".join(detail_lines),
                                "source_type": "phone",
                                "icon": "🏢",
                                "verified": True,
                                "subject_id": subject_id,
                                "screenshots": [
                                    {
                                        "url": wa_info.get("contact_profile_pic"),
                                        "source_url": None,
                                    }
                                ]
                                if wa_info.get("contact_profile_pic")
                                else [],
                            }
                        )
        except Exception:
            logger.debug("WhatsApp 2Chat fallback failed for %s", phone, exc_info=True)

    return findings
