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
                    headers={"ApiKey": overheid_key},
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

        # Brave Search
        brave_key = Setting.get("brave_api_key", "")
        if brave_key:
            try:
                jitter_sleep(domain_hint="https://api.search.brave.com")
                r = curl_requests.get(
                    "https://api.search.brave.com/res/v1/web/search?q=test&count=1",
                    headers={
                        "X-Subscription-Token": brave_key,
                        "Accept": "application/json",
                    },
                    impersonate="chrome124",
                    timeout=5,
                )
                result["brave"] = (
                    "ok" if r.status_code == 200 else f"unexpected: {r.status_code}"
                )
            except Exception as e:
                result["brave"] = f"unavailable: {e}"
        else:
            result["brave"] = "no key configured"

        # Tor
        tor_enabled = Setting.get("tor_enabled", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        if tor_enabled:
            tor_proxy = Setting.get("tor_proxy", "socks5://127.0.0.1:9050")
            if brave_key:
                try:
                    with curl_requests.Session(
                        impersonate="chrome124",
                        proxies={"http": tor_proxy, "https": tor_proxy},
                        timeout=5.0,
                    ) as c:
                        r = c.get(
                            "https://api.search.brave.com/res/v1/web/search?q=test&count=1",
                            headers={
                                "X-Subscription-Token": brave_key,
                                "Accept": "application/json",
                            },
                        )
                    result["tor"] = (
                        "ok"
                        if r.status_code == 200
                        else f"brave_via_tor: {r.status_code}"
                    )
                except Exception as e:
                    result["tor"] = f"unavailable: {e}"
            else:
                result["tor"] = "no_brave_key"
        else:
            result["tor"] = "disabled"

    return result
