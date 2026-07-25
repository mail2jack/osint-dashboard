"""Shared health check utilities for external OSINT services."""

import logging

from cms.services.http_utils import jittered_get, is_tor_enabled

from cms.models import db, Setting

logger = logging.getLogger(__name__)

_TOR_CHECK_TIMEOUT = 10
_TORCHECK_URL = "https://check.torproject.org"


def check_external_services(quick: bool = False) -> dict:
    """Check external OSINT service health.

    When *quick* is True, skips kadaster, rdw, and hibp checks.
    Returns a dict of ``{service_name: "ok"|"error: ..."|"disabled"}``.
    """
    from cms.spiderfoot_service import check_spiderfoot_health

    result = {}

    # Database
    try:
        db.session.execute(db.text("SELECT 1"))
        result["database"] = "ok"
    except Exception as e:
        result["database"] = f"error: {e}"

    # SpiderFoot
    try:
        healthy, msg = check_spiderfoot_health()
        result["spiderfoot"] = "ok" if healthy else msg
    except Exception as e:
        result["spiderfoot"] = f"unavailable: {e}"

    # External HTTP services (skipped on quick check)
    if not quick:
        for svc_name, svc_url, svc_check in [
            (
                "rdw",
                "https://opendata.rdw.nl/resource/m9d7-ebf2.json",
                lambda r: r.status_code in (200, 401, 403),
            ),
            (
                "kadaster",
                "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free?q=test&rows=1",
                lambda r: r.status_code == 200,
            ),
            (
                "hibp",
                "https://haveibeenpwned.com",
                lambda r: r.status_code in (200, 403),
            ),
        ]:
            try:
                r = jittered_get(svc_url, timeout=5)
                result[svc_name] = (
                    "ok" if svc_check(r) else f"unexpected: {r.status_code}"
                )
            except Exception as e:
                result[svc_name] = f"unavailable: {e}"

        # Overheid.io
        overheid_key = Setting.get("overheid_api_key", "")
        if overheid_key:
            try:
                r = jittered_get(
                    "https://api.overheid.io/v3/openkvk?query=test&size=1",
                    headers={"ovio-api-key": overheid_key},
                    timeout=5,
                )
                result["overheid"] = (
                    "ok"
                    if r.status_code in (200, 422)
                    else (
                        "auth error"
                        if r.status_code == 401
                        else f"unexpected: {r.status_code}"
                    )
                )
            except Exception as e:
                result["overheid"] = f"unavailable: {e}"
        else:
            result["overheid"] = "no key configured"

        # Brave Search — key presence only; no live call to avoid burning quota
        brave_key = Setting.get("brave_api_key", "")
        if brave_key:
            result["brave"] = "ok"
        else:
            result["brave"] = "no key configured"

        # Tor — active connection test via check.torproject.org
        if is_tor_enabled():
            try:
                r = jittered_get(
                    _TORCHECK_URL,
                    timeout=_TOR_CHECK_TIMEOUT,
                )
                if "Congratulations" in r.text:
                    result["tor"] = "ok"
                elif r.status_code == 200:
                    result["tor"] = "not_using_tor"
                else:
                    result["tor"] = f"unexpected: {r.status_code}"
            except Exception as e:
                result["tor"] = f"unavailable: {e}"
        else:
            result["tor"] = "disabled"

    return result
