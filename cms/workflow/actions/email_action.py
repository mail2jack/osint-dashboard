import logging

from cms.services.http_utils import jittered_get
from cms.workflow.actions.helpers import _first_subject, _get_api_key

logger = logging.getLogger(__name__)


def _email_check(action):
    subject = _first_subject(action)
    subject_id = subject.id if subject else None
    email = action.data_value if action.data_value else None
    if not email:
        email = subject.email if subject else None
    if not email:
        return []
    findings = []
    from cms.email_search import lookup_email

    try:
        result = lookup_email(email)
        confirmed = [
            a for a in result.get("account_checks", []) if a.get("exists") == True
        ]
        unverified = [
            a for a in result.get("account_checks", []) if a.get("exists") is None
        ]
        if confirmed:
            for acct in confirmed:
                site_name = acct.get("name") or acct.get("site") or "unknown"
                findings.append(
                    {
                        "title": f"Account found on {site_name}",
                        "detail": f"Email {email} is registered on {site_name}. "
                        f"Status: {acct.get('status', 'unknown')}",
                        "source_url": acct.get("url"),
                        "source_type": "email",
                        "icon": "📧",
                        "verified": acct.get("verified", False),
                        "subject_id": subject_id,
                        "screenshots": [{"url": None, "source_url": acct.get("url")}],
                    }
                )
        if unverified:
            findings.append(
                {
                    "title": f"{len(unverified)} site(s) respond but account not confirmed",
                    "detail": f"For {email}, {len(unverified)} site(s) responded with 200 status, "
                    f"but the page contained no confirmation of the account. "
                    f"These are likely false positives.",
                    "source_type": "email",
                    "icon": "⚠️",
                    "verified": False,
                    "subject_id": subject_id,
                }
            )
        if not confirmed and not unverified:
            findings.append(
                {
                    "title": "No accounts found via email search",
                    "detail": f"No registered accounts found for {email}.",
                    "source_type": "email",
                    "icon": "📧",
                    "verified": False,
                    "subject_id": subject_id,
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"Email check failed: {e}",
                "detail": str(e),
                "source_type": "email",
                "icon": "📧",
                "verified": False,
                "subject_id": subject_id,
            }
        )

    hibp_key = _get_api_key("hibp_api_key")
    if hibp_key:
        try:
            r = jittered_get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                headers={"hibp-api-key": hibp_key, "user-agent": "Iveras-Workflow"},
                timeout=15,
            )
            if r.status_code == 200:
                for breach in r.json():
                    findings.append(
                        {
                            "title": f"Data breach: {breach['Name']}",
                            "detail": f"Domain: {breach.get('Domain', '')}. "
                            f"Data: {', '.join(breach.get('DataClasses', []))}. "
                            f"Date: {breach.get('BreachDate', '')}",
                            "source_url": f"https://haveibeenpwned.com/account/{email}",
                            "source_type": "hibp",
                            "icon": "🔓",
                            "verified": False,
                            "subject_id": subject_id,
                            "screenshots": [
                                {
                                    "url": None,
                                    "source_url": f"https://haveibeenpwned.com/account/{email}",
                                }
                            ],
                        }
                    )
        except Exception as e:
            logger.warning("HIBP check failed: %s", e)

    # — PGP keyserver check
    try:
        from urllib.parse import quote

        pgp_url = f"https://keys.openpgp.org/vks/v1/by-email/{quote(email)}"
        pgp_resp = jittered_get(pgp_url, timeout=10)
        if pgp_resp.status_code == 200:
            findings.append(
                {
                    "title": "PGP key found",
                    "detail": (
                        f"A PGP public key was found for {email} on "
                        f"keys.openpgp.org. This confirms that the owner "
                        f"uses PGP encryption for email communication."
                    ),
                    "source_url": f"https://keys.openpgp.org/search?q={quote(email)}",
                    "source_type": "pgp",
                    "icon": "🔐",
                    "verified": True,
                    "subject_id": subject_id,
                    "screenshots": [
                        {
                            "url": None,
                            "source_url": f"https://keys.openpgp.org/search?q={quote(email)}",
                        }
                    ],
                }
            )
    except Exception as e:
        logger.debug("PGP keyserver check failed for %s: %s", email, e)

    # — Brave Search email context
    brave_key = _get_api_key("brave_api_key")
    if brave_key:
        try:
            from cms.services.search_service import brave_search

            ctx_results = brave_search(email, api_key=brave_key)
            for res in ctx_results[:10]:
                findings.append(
                    {
                        "title": f"Mentioned on: {res.get('title', 'unknown')[:200]}",
                        "detail": res.get("description", "")[:300],
                        "source_url": res.get("url"),
                        "source_type": "email_context",
                        "icon": "🔍",
                        "verified": False,
                        "subject_id": subject_id,
                        "screenshots": [{"url": None, "source_url": res.get("url")}],
                    }
                )
        except Exception as e:
            logger.debug("Brave email context search failed for %s: %s", email, e)
    return findings
