import logging
import re
import socket
import os
import concurrent.futures

import flask
from flask import request, jsonify, current_app
from flask_login import login_required

from . import cms_bp
from .. import csrf
from ..validation import validate, EmailCheckSchema
from ..rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from ..api_key_auth import api_key_required
from cms.services.http_utils import jittered_get

logger = logging.getLogger(__name__)

_DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwaway.email",
    "yopmail.com",
    "sharklasers.com",
    "trashmail.com",
    "10minutemail.com",
    "mailnator.com",
    "temp-mail.org",
    "getairmail.com",
    "tempinbox.com",
    "spamgourmet.com",
    "mailexpire.com",
    "maildrop.cc",
    "burnermail.io",
    "inboxbear.com",
    "discard.email",
    "mintemail.com",
    "mailforspam.com",
}


def _email_check_sync(email: str) -> dict:
    """Perform the full email check (HIBP, MX, disposable, emailrep) synchronously."""
    result = {
        "email": email,
        "valid_format": False,
        "domain": None,
        "has_mx": False,
        "disposable": False,
        "hibp_found": False,
        "hibp_breaches": [],
        "emailrep": None,
        "search_links": [],
        "error": None,
    }

    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, email):
        result["error"] = "Invalid email format"
        return result

    result["valid_format"] = True
    domain = email.split("@")[1]
    result["domain"] = domain

    if domain.lower() in _DISPOSABLE_DOMAINS:
        result["disposable"] = True

    try:
        socket.getaddrinfo(domain, 25)
        result["has_mx"] = True
    except socket.gaierror:
        result["has_mx"] = False

    from ..models import Setting

    hibp_key = Setting.get("hibp_api_key", "") or os.environ.get("HIBP_API_KEY", "")
    if hibp_key:
        try:
            resp = jittered_get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=false",
                headers={
                    "hibp-api-key": hibp_key,
                    "User-Agent": "Iveras-OSINT-Dashboard/3.0",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                breaches = resp.json()
                result["hibp_found"] = True
                result["hibp_breaches"] = [
                    {
                        "name": b.get("Name"),
                        "domain": b.get("Domain"),
                        "date": b.get("BreachDate"),
                        "data_classes": b.get("DataClasses", []),
                        "description": b.get("Description", "")[:200],
                    }
                    for b in breaches
                ]
            elif resp.status_code == 404:
                result["hibp_found"] = False
            elif resp.status_code == 401:
                result["hibp_found"] = False
                logger.warning("HIBP API key rejected")
        except Exception as e:
            logger.debug(f"HIBP check failed ({type(e).__name__}): {e}")
            result["error"] = f"HIBP check failed: {type(e).__name__}"

    try:
        eresp = jittered_get(
            f"https://emailrep.io/{email}",
            headers={"User-Agent": "Iveras-OSINT-Dashboard/3.0"},
            timeout=10,
        )
        if eresp.status_code == 200:
            result["emailrep"] = eresp.json()
    except Exception as e:
        logger.debug(f"EmailRep lookup failed ({type(e).__name__}): {e}")

    result["search_links"] = [
        {
            "label": "Have I Been Pwned",
            "url": f"https://haveibeenpwned.com/account/{email}",
        },
        {"label": "EmailRep", "url": f"https://emailrep.io/{email}"},
        {"label": "Hunter.io", "url": f"https://hunter.io/search/{domain}"},
        {"label": "Dehashed", "url": f"https://dehashed.com/search?query={email}"},
        {"label": "Google", "url": f"https://www.google.com/search?q={email}"},
    ]
    return result


@cms_bp.route("/api/email-check", methods=["POST"])
@csrf.exempt
@api_key_required
@login_required
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="email_check")
@validate(EmailCheckSchema)
def email_check() -> flask.Response:
    """Validate an email address and check for known breaches."""
    email = request.validated_data["email"].strip().lower()
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            return _email_check_sync(email)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            result = future.result(timeout=25)
    except concurrent.futures.TimeoutError:
        return jsonify({"error": "Email check timed out, try again later"}), 504
    except Exception as e:
        logger.warning(f"Email check failed ({type(e).__name__}): {e}")
        return jsonify({"error": f"Email check failed: {type(e).__name__}"}), 500

    logger.debug(
        f"Email check: {email} -> valid={result['valid_format']}, mx={result['has_mx']}, hibp={result['hibp_found']}"
    )
    return jsonify(result), 200
