import logging
import os

logger = logging.getLogger(__name__)


def _get_overheid_key():
    try:
        from flask import current_app as app

        with app.app_context():
            from cms.models import Setting

            key = Setting.get("overheid_api_key", "")
            if key:
                return key
    except Exception as e:
        logger.debug(f"_get_overheid_key failed ({type(e).__name__}): {e}")
    return os.environ.get("OVERHEID_API_KEY", "")


def _get_twochat_credentials():
    try:
        from flask import current_app as app

        with app.app_context():
            from cms.models import Setting

            api_key = Setting.get("twochat_api_key", "")
            number = Setting.get("twochat_whatsapp_number", "")
            if api_key and number:
                return api_key, number
    except Exception as e:
        logger.debug(f"_get_twochat_credentials failed ({type(e).__name__}): {e}")
    return os.environ.get("TWOCHAT_API_KEY", ""), os.environ.get(
        "TWOCHAT_WHATSAPP_NUMBER", ""
    )


def _get_brave_key():
    try:
        from flask import current_app as app

        with app.app_context():
            from cms.models import Setting

            key = Setting.get("brave_api_key", "")
            if key:
                return key
    except Exception as e:
        logger.debug(f"_get_brave_key failed ({type(e).__name__}): {e}")
    return os.environ.get("BRAVE_API_KEY", "")


def _get_hibp_key():
    try:
        from flask import current_app as app

        with app.app_context():
            from cms.models import Setting

            key = Setting.get("hibp_api_key", "")
            if key:
                return key
    except Exception as e:
        logger.debug(f"_get_hibp_key failed ({type(e).__name__}): {e}")
    return os.environ.get("HIBP_API_KEY", "")


__all__ = [
    "_get_overheid_key",
    "_get_twochat_credentials",
    "_get_brave_key",
    "_get_hibp_key",
]
