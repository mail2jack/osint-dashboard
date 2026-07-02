"""Shared health check utilities for external OSINT services."""

import logging

from curl_cffi import requests as curl_requests
from cms.services.http_utils import jitter_sleep

from cms.models import db, Setting

logger = logging.getLogger(__name__)


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
            ("hibp", "https://haveibeenpwned.com", lambda r: r.status_code == 200),
        ]:
            try:
                jitter_sleep(domain_hint=svc_url)
                r = curl_requests.get(svc_url, impersonate="chrome124", timeout=5)
                result[svc_name] = (
                    "ok" if svc_check(r) else f"unexpected: {r.status_code}"
                )
            except Exception as e:
                result[svc_name] = f"unavailable: {e}"

        # Overheid.io
        overheid_key = Setting.get("overheid_api_key", "")
        if overheid_key:
            try:
                jitter_sleep(domain_hint="https://api.overheid.io")
                r = curl_requests.get(
                    "https://api.overheid.io/v3/openkvk?query=test&size=1",
                    headers={"ovio-api-key": overheid_key},
                    impersonate="chrome124",
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

        # Tor — requires Brave key via Tor; skip live call for same reason
        tor_enabled = Setting.get("tor_enabled", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        if tor_enabled:
            if brave_key:
                result["tor"] = "ok"
            else:
                result["tor"] = "no_brave_key"
        else:
            result["tor"] = "disabled"

    return result
