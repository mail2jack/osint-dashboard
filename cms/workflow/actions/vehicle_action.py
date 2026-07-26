import logging
import re

from cms.services.http_utils import jittered_get
from cms.workflow.actions.helpers import _first_subject

logger = logging.getLogger(__name__)


def _rdw_check(action):
    findings = []
    subject = _first_subject(action)
    ipc = action.data_value if action.data_value else None
    if not ipc:
        ipc = getattr(subject, "identification_number", None) if subject else None
    subject_id = subject.id if subject else None
    if not ipc:
        findings.append(
            {
                "title": "No license plate provided for RDW check",
                "detail": "Add a license plate to the subject via the license plate field.",
                "source_type": "rdw",
                "icon": "🚗",
                "verified": False,
                "subject_id": subject_id,
            }
        )
        return findings
    try:
        kenteken = ipc.replace("-", "").replace(" ", "").upper()
        r = jittered_get(
            "https://opendata.rdw.nl/resource/m9d7-ebf2.json",
            params={"kenteken": kenteken},
            timeout=15,
        )
        if r.status_code == 200 and r.json():
            data = dict(r.json()[0])

            def _fmt(v):
                return (
                    v.replace("-", "").replace("T", " ")[:10]
                    if v and ("-" in v or "T" in v)
                    else v
                )

            try:
                prijs = data.get("catalogusprijs", "")
                if prijs:
                    data["_prijs_eur"] = f"€ {int(prijs):,}".replace(",", ".")
            except (ValueError, TypeError):
                pass
            detail_parts = []
            if data.get("kenteken"):
                detail_parts.append(f"License plate: {data['kenteken']}")
            if data.get("eerste_kleur"):
                detail_parts.append(f"Color: {data['eerste_kleur']}")
            if data.get("vermogen_massario"):
                detail_parts.append(f"{data['vermogen_massario']} kW")
            if data.get("brandstof_omschrijving"):
                detail_parts.append(data["brandstof_omschrijving"])
            if data.get("vervaldatum_apk"):
                detail_parts.append(f"APK: {_fmt(data['vervaldatum_apk'])}")
            findings.append(
                {
                    "title": f"🚗 {data.get('merk', 'unknown')} {data.get('handelsbenaming', '')}",
                    "detail": " · ".join(detail_parts),
                    "source_type": "rdw",
                    "icon": "🚗",
                    "verified": False,
                    "subject_id": subject_id,
                    "raw_data": data,
                }
            )
        else:
            findings.append(
                {
                    "title": f"No RDW data for license plate {kenteken}",
                    "detail": "Vehicle not found in RDW registration.",
                    "source_type": "rdw",
                    "icon": "🚗",
                    "verified": False,
                    "subject_id": subject_id,
                }
            )
    except Exception as e:
        findings.append(
            {
                "title": f"RDW check failed: {e}",
                "detail": str(e),
                "source_type": "rdw",
                "icon": "🚗",
                "verified": False,
                "subject_id": subject_id,
            }
        )
    return findings


def _vessel_check(action):
    findings = []

    identifier = action.data_value if action.data_value else None
    subject = _first_subject(action)

    imo = mmsi = eni = name = None
    if identifier:
        cleaned = identifier.strip()
        if re.match(r"^(IMO\s*)?\d{7}$", cleaned, re.IGNORECASE):
            imo = re.sub(r"(?i)^IMO\s*", "", cleaned)
        elif re.match(r"^\d{9}$", cleaned):
            mmsi = cleaned
        elif re.match(r"^\d{8,9}$", cleaned):
            eni = cleaned
        else:
            name = cleaned
        if subject:
            name = name or subject.name
    else:
        if subject and subject.subject_type == "vessel":
            imo = subject.imo_number or None
            mmsi = subject.mmsi or None
            eni = subject.eni_number or None
            name = subject.name

            # Fallback to identification_number (workflow IMO-veld)
            if not imo and subject.identification_number:
                raw = subject.identification_number
                if re.match(r"^\d{7}$", raw.strip()):
                    imo = raw.strip()
                else:
                    name = name or raw

            # Fallback to vessel_data
            if not any([imo, mmsi, eni]) and subject.vessel_data:
                vd = subject.vessel_data
                imo = imo or vd.get("imo")
                mmsi = mmsi or vd.get("mmsi")
                eni = eni or vd.get("eni")
                name = name or vd.get("name")

    if not any([imo, mmsi, eni, name]):
        findings.append(
            {
                "title": "No vessel data provided",
                "detail": "Enter an IMO, MMSI, ENI number or vessel name, "
                "or link a vessel subject to this investigation first.",
                "source_type": "vessel",
                "icon": "🚢",
                "verified": False,
                "subject_id": subject.id if subject else None,
            }
        )
        return findings

    try:
        from cms.vessel_service import lookup_vessel

        result = lookup_vessel(imo=imo, mmsi=mmsi, eni=eni, name=name)

        if result.get("found"):
            parts = []
            if result.get("name"):
                parts.append(f"Name: {result['name']}")
            if result.get("imo"):
                parts.append(f"IMO: {result['imo']}")
            if result.get("mmsi"):
                parts.append(f"MMSI: {result['mmsi']}")
            if result.get("eni"):
                parts.append(f"ENI: {result['eni']}")
            if result.get("flag"):
                parts.append(f"Flag: {result['flag']}")
            if result.get("ship_type"):
                parts.append(f"Type: {result['ship_type']}")
            if result.get("length"):
                parts.append(f"Length: {result['length']} m")
            if result.get("beam"):
                parts.append(f"Beam: {result['beam']} m")
            if result.get("year_built"):
                parts.append(f"Year built: {result['year_built']}")
            if result.get("callsign"):
                parts.append(f"Callsign: {result['callsign']}")
            if result.get("destination"):
                parts.append(f"Destination: {result['destination']}")
            if result.get("builder"):
                parts.append(f"Builder: {result['builder']}")
            if result.get("position"):
                pos = result["position"]
                lat, lon = pos.get("lat", "?"), pos.get("lon", "?")
                parts.append(
                    f"Position: {lat}, {lon} (https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12)"
                )
            if result.get("position_text"):
                from urllib.parse import quote

                map_q = quote(result["position_text"])
                parts.append(
                    f"Position: {result['position_text']} (https://www.google.com/maps?q={map_q})"
                )
            if result.get("speed"):
                parts.append(f"Speed: {result['speed']} knots")
            if result.get("course"):
                parts.append(f"Course: {result['course']}°")
            if result.get("navigation_status"):
                parts.append(f"Status: {result['navigation_status']}")
            if result.get("draught"):
                parts.append(f"Draught: {result['draught']} m")
            if result.get("eta"):
                parts.append(f"ETA: {result['eta']}")

            sources = result.get("sources", [])
            if sources:
                parts.append(f"\nSources: {', '.join(sources)}")

            findings.append(
                {
                    "title": f"🚢 {result.get('name', 'Unknown vessel')}",
                    "detail": " · ".join(parts) if parts else "Data found",
                    "source_type": "vessel",
                    "icon": "🚢",
                    "verified": False,
                    "raw_data": {
                        "imo": result.get("imo"),
                        "mmsi": result.get("mmsi"),
                        "eni": result.get("eni"),
                        "name": result.get("name"),
                        "flag": result.get("flag"),
                        "ship_type": result.get("ship_type"),
                        "length": result.get("length"),
                        "beam": result.get("beam"),
                        "year_built": result.get("year_built"),
                        "callsign": result.get("callsign"),
                        "destination": result.get("destination"),
                        "builder": result.get("builder"),
                        "position": result.get("position"),
                        "position_text": result.get("position_text"),
                        "speed": result.get("speed"),
                        "course": result.get("course"),
                        "navigation_status": result.get("navigation_status"),
                        "eta": result.get("eta"),
                        "draught": result.get("draught"),
                        "sources": sources,
                        "source_data": result.get("source_data", {}),
                    },
                }
            )
        else:
            findings.append(
                {
                    "title": "No vessel data found",
                    "detail": "No maritime sources yielded data for the provided identifiers.",
                    "source_type": "vessel",
                    "icon": "🚢",
                    "verified": False,
                    "subject_id": subject.id if subject else None,
                }
            )
    except Exception as e:
        logger.exception("Vessel check failed")
        findings.append(
            {
                "title": f"Vessel check failed: {e}",
                "detail": str(e),
                "source_type": "vessel",
                "icon": "🚢",
                "verified": False,
            }
        )
    return findings
