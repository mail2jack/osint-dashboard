"""
SMS/WhatsApp notification delivery via Twilio.

Twilio credentials are read from the ``Setting`` table:
- ``twilio_account_sid``
- ``twilio_auth_token``
- ``twilio_phone_number`` (sender number for SMS)
- ``twilio_whatsapp_number`` (sender number for WhatsApp, e.g. ``+14155238886``)

When Twilio is not configured, all send functions return ``False`` with a warning.
"""

import logging

logger = logging.getLogger(__name__)

TWILIO_AVAILABLE = False
try:
    from twilio.rest import Client as TwilioClient
    from twilio.base.exceptions import TwilioRestException

    TWILIO_AVAILABLE = True
except ImportError:
    TwilioClient = None  # type: ignore
    TwilioRestException = None  # type: ignore


def _get_twilio_config() -> dict:
    """Read Twilio credentials from the Setting table."""
    from .models import Setting

    return {
        "account_sid": Setting.get("twilio_account_sid") or "",
        "auth_token": Setting.get("twilio_auth_token") or "",
        "phone_number": Setting.get("twilio_phone_number") or "",
        "whatsapp_number": Setting.get("twilio_whatsapp_number") or "",
    }


def _get_twilio_client():
    """Return a Twilio REST client or None if not configured."""
    cfg = _get_twilio_config()
    if not cfg["account_sid"] or not cfg["auth_token"]:
        return None
    if not TWILIO_AVAILABLE:
        logger.warning("Twilio library not installed")
        return None
    return TwilioClient(cfg["account_sid"], cfg["auth_token"])


def send_sms(to_phone: str, message: str) -> bool:
    """Send an SMS via Twilio.

    Returns True on success, False on failure/not-configured.
    """
    client = _get_twilio_client()
    if not client:
        logger.warning("Twilio not configured — cannot send SMS")
        return False

    cfg = _get_twilio_config()
    from_number = cfg.get("phone_number", "")
    if not from_number:
        logger.warning("Twilio SMS sender number not configured")
        return False

    try:
        client.messages.create(body=message, from_=from_number, to=to_phone)
        logger.info("SMS sent to %s", to_phone)
        return True
    except TwilioRestException as e:
        logger.error("Twilio SMS error for %s: %s", to_phone, e)
        return False
    except Exception as e:
        logger.error("Failed to send SMS to %s: %s", to_phone, e)
        return False


def send_whatsapp(to_phone: str, message: str) -> bool:
    """Send a WhatsApp message via Twilio.

    *to_phone* should be a phone number in E.164 format (e.g. ``+31612345678``).

    Returns True on success, False on failure/not-configured.
    """
    client = _get_twilio_client()
    if not client:
        logger.warning("Twilio not configured — cannot send WhatsApp")
        return False

    cfg = _get_twilio_config()
    from_number = cfg.get("whatsapp_number", "")
    if not from_number:
        logger.warning("Twilio WhatsApp sender number not configured")
        return False

    # Twilio WhatsApp numbers must be prefixed with "whatsapp:"
    from_whatsapp = f"whatsapp:{from_number}"
    to_whatsapp = f"whatsapp:{to_phone}"

    try:
        client.messages.create(body=message, from_=from_whatsapp, to=to_whatsapp)
        logger.info("WhatsApp sent to %s", to_phone)
        return True
    except TwilioRestException as e:
        logger.error("Twilio WhatsApp error for %s: %s", to_phone, e)
        return False
    except Exception as e:
        logger.error("Failed to send WhatsApp to %s: %s", to_phone, e)
        return False
