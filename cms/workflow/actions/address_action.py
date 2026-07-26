import logging

from cms.services.http_utils import jittered_get
from cms.workflow.actions.helpers import _first_subject

logger = logging.getLogger(__name__)


def _address_check(action):
    findings = []
    subject = _first_subject(action)
    subject_id = subject.id if subject else None
    address_query = action.data_value if action.data_value else None
    if not address_query:
        parts = []
        if subject:
            if subject.street:
                addr = f"{subject.street} {subject.house_number or ''}{subject.house_number_addition or ''}".strip()
                parts.append(addr)
            if subject.postal_code or subject.city:
                parts.append(
                    f"{subject.postal_code or ''} {subject.city or ''}".strip()
                )
        address_query = ", ".join(parts) if parts else None
    if not address_query:
        findings.append(
            {
                "title": "No address details provided",
                "detail": "Enter street, house number, postal code and city for the subject.",
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
                "subject_id": subject_id,
            }
        )
        return findings
    try:
        r = jittered_get(
            "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free",
            params={"q": address_query, "rows": 10},
            timeout=10,
        )
        data = r.json()
        docs = (data.get("response", {}) or {}).get("docs", [])

        # Filter only address-level results, sorted by score descending
        adres_docs = [d for d in docs if d.get("type") == "adres"]
        adres_docs.sort(key=lambda d: d.get("score") or 0, reverse=True)

        for doc in adres_docs[:1]:
            nummeraanduiding = doc.get("nummeraanduiding_id") or doc.get("id", "")
            bag_url = f"https://bagviewer.kadaster.nl/lvbag/bag-viewer/?searchQuery={doc.get('weergavenaam', address_query)}&objectId={nummeraanduiding}&theme=BRT+Achtergrond&zoomlevel=16"

            details = []
            if doc.get("straatnaam"):
                hn = doc.get("huisnummer", "")
                hnl = doc.get("huis_nlt", "")
                hn_display = hnl if hnl else hn
                details.append(f"Address: {doc['straatnaam']} {hn_display}")
            if doc.get("postcode"):
                details.append(f"Postal code: {doc['postcode']}")
            if doc.get("woonplaatsnaam"):
                details.append(f"City: {doc['woonplaatsnaam']}")
            if doc.get("buurtnaam"):
                details.append(f"Neighborhood: {doc['buurtnaam']}")
            if doc.get("wijknaam"):
                details.append(f"District: {doc['wijknaam']}")
            if doc.get("gemeentenaam"):
                details.append(f"Municipality: {doc['gemeentenaam']}")
            if doc.get("provincienaam"):
                details.append(f"Province: {doc['provincienaam']}")
            if doc.get("gekoppeld_perceel"):
                percelen = "; ".join(doc["gekoppeld_perceel"])
                details.append(f"Cadastral parcel: {percelen}")
            if doc.get("gekoppeld_appartement"):
                apps = "; ".join(doc["gekoppeld_appartement"])
                details.append(f"Apartment right: {apps}")
            if doc.get("openbareruimtetype"):
                details.append(f"Public space type: {doc['openbareruimtetype']}")

            findings.append(
                {
                    "title": f"Adres: {doc.get('weergavenaam', address_query)}",
                    "detail": "\n".join(details)
                    if details
                    else "BAG registration found.",
                    "source_url": bag_url,
                    "source_type": "kadaster",
                    "icon": "🏠",
                    "verified": False,
                    "subject_id": subject_id,
                    "screenshots": [{"url": None, "source_url": bag_url}],
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"Address check failed: {e}",
                "detail": str(e),
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
                "subject_id": subject_id,
            }
        )
    if not findings:
        findings.append(
            {
                "title": f"Adres: {address_query}",
                "detail": "Kadaster lookup returned no results. Possibly not found in BAG.",
                "source_type": "kadaster",
                "icon": "🏠",
                "verified": False,
                "subject_id": subject_id,
            }
        )
    return findings
