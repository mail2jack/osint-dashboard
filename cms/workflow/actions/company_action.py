import json
import logging
import re

from cms.services.http_utils import jittered_get
from cms.workflow.actions.helpers import _first_subject, _get_api_key

logger = logging.getLogger(__name__)


def _kvk_check(action):
    findings = []
    raw = action.data_value if action.data_value else None
    subject = _first_subject(action)
    subject_id = subject.id if subject else None
    query = ""
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                variables = payload.get("variables", {})
                query = (
                    variables.get("company", "")
                    or variables.get("name", "")
                    or payload.get("query", "")
                )
        except (json.JSONDecodeError, TypeError):
            query = raw
    if not query:
        query = subject.name if subject else ""
    if not query:
        return findings

    api_key = _get_api_key("overheid_api_key")
    if api_key:
        try:
            r = jittered_get(
                "https://api.overheid.io/openkvk/zoeken",
                params={"q": query, "rows": 10},
                headers={"ovio-api-key": api_key},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else data.get("data", [])
                query_lower = query.lower()
                for item in items:
                    naam = item.get("naam", "")
                    if query_lower not in naam.lower():
                        continue
                    findings.append(
                        {
                            "title": f"KvK: {naam}",
                            "detail": f"KvK: {item.get('kvkNummer', '')}. "
                            f"Address: {item.get('straat', '')} {item.get('huisnummer', '')}, "
                            f"{item.get('postcode', '')} {item.get('plaats', '')}. "
                            f"Legal form: {item.get('rechtsvorm', '')}",
                            "source_url": f"https://www.kvk.nl/zoeken/?q={item.get('kvkNummer', query)}",
                            "source_type": "kvk",
                            "icon": "🏢",
                            "verified": False,
                            "subject_id": subject_id,
                            "screenshots": [
                                {
                                    "url": None,
                                    "source_url": f"https://www.kvk.nl/zoeken/?q={item.get('kvkNummer', query)}",
                                }
                            ],
                        }
                    )
                if findings:
                    return findings
            else:
                logger.warning(
                    f"KvK API gaf status {r.status_code}, vallen terug op openkvk.nl"
                )
        except Exception as e:
            logger.warning(f"KvK API exceptie: {e}, vallen terug op openkvk.nl")

    # Fallback: scrape openkvk.nl (geen API key nodig)
    try:
        import html as _html

        r = jittered_get(
            "https://openkvk.nl/search",
            params={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            timeout=15,
        )
        if r.status_code != 200:
            findings.append(
                {
                    "title": "KvK lookup openkvk.nl failed",
                    "detail": f"Status {r.status_code}",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                    "subject_id": subject_id,
                }
            )
            return findings

        snap_match = re.search(r'wire:snapshot="([^"]+)"', r.text)
        if not snap_match:
            findings.append(
                {
                    "title": "KvK lookup via openkvk.nl",
                    "detail": "No results found on openkvk.nl.",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                    "subject_id": subject_id,
                }
            )
            return findings

        snap_json = _html.unescape(snap_match.group(1))
        snap_data = json.loads(snap_json)
        companies_data = snap_data.get("data", {}).get("companiesData", [])
        if not companies_data or not companies_data[0]:
            findings.append(
                {
                    "title": "KvK lookup via openkvk.nl",
                    "detail": "No companies found.",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                    "subject_id": subject_id,
                }
            )
            return findings

        seen_kvk = set()
        for pair in companies_data[0][:5]:
            company = pair[0] if isinstance(pair, list) else pair
            kvk_nummer = company.get("kvknummer", "")
            if kvk_nummer and kvk_nummer in seen_kvk:
                continue
            if kvk_nummer:
                seen_kvk.add(kvk_nummer)

            naam = company.get("naam", "unknown")
            loc = company.get("bezoeklocatie", [{}])
            loc = loc[0] if isinstance(loc, list) and loc else loc
            straat = loc.get("straat", "") if isinstance(loc, dict) else ""
            huisnr = loc.get("huisnummer", "") if isinstance(loc, dict) else ""
            postcode = loc.get("postcode", "") if isinstance(loc, dict) else ""
            plaats = loc.get("plaats", "") if isinstance(loc, dict) else ""
            rechtsvorm = company.get("rechtsvormOmschrijving", "")
            handelsnamen = company.get("huidigeHandelsNamen", [])
            if isinstance(handelsnamen, list) and handelsnamen:
                handelsnamen = [h for h in handelsnamen if isinstance(h, str)]
            extra = ""
            if handelsnamen and len(handelsnamen) > 1:
                extra = f"Trade names: {', '.join(handelsnamen[:5])}."

            detail_parts = []
            if kvk_nummer:
                detail_parts.append(f"KvK: {kvk_nummer}")
            addr = " ".join(p for p in [straat, huisnr] if p)
            if addr and postcode and plaats:
                detail_parts.append(f"{addr}, {postcode} {plaats}")
            if rechtsvorm:
                detail_parts.append(rechtsvorm)
            if extra:
                detail_parts.append(extra)
            detail = ". ".join(detail_parts)

            findings.append(
                {
                    "title": f"KvK: {naam}",
                    "detail": detail,
                    "source_url": f"https://openkvk.nl/search?q={kvk_nummer or query}",
                    "source_type": "kvk",
                    "icon": "🏢",
                    "verified": False,
                    "subject_id": subject_id,
                    "screenshots": [
                        {
                            "url": None,
                            "source_url": f"https://www.kvk.nl/zoeken/?q={kvk_nummer or query}",
                        }
                    ],
                }
            )

    except Exception as e:
        findings.append(
            {
                "title": "KvK lookup via openkvk.nl failed",
                "detail": str(e),
                "source_type": "kvk",
                "icon": "🏢",
                "verified": False,
                "subject_id": subject_id,
            }
        )

    return findings
