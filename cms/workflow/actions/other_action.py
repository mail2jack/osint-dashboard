import json
import logging
import os
import threading

import requests
from cms.models import db
from cms.services.http_utils import jittered_get
from cms.workflow.actions.helpers import _action_subject, _get_api_key

logger = logging.getLogger(__name__)


def _financial_check(action):
    """Placeholder — financieel onderzoek is handmatig in deze fase."""
    subject = _action_subject(action)
    return [
        {
            "title": "Financial research — manual action",
            "detail": "Financial research requires manual request to banks/supervisors. "
            "This action serves as a reminder to request bank statements and transaction overviews "
            "from the relevant institutions.",
            "source_type": "financial",
            "icon": "💰",
            "verified": False,
            "subject_id": subject.id if subject else None,
        }
    ]


def _query_crtsh(domain):
    """Query crt.sh for subdomains via certificate transparency logs."""
    from cms.constants import HEADERS

    url = f"https://crt.sh/?q=%25.{domain}&output=json"
    headers = dict(HEADERS)
    headers["Accept"] = "application/json"
    subs = set()

    for attempt in range(2):
        try:
            resp = jittered_get(url, headers=headers, timeout=60)
            if resp.status_code in (502, 503, 504):
                threading.Event().wait(2)
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    body_len = len(
                        getattr(resp, "text", "") or getattr(resp, "content", b"") or ""
                    )
                    raise ValueError(
                        f"crt.sh returned no valid JSON ({body_len} bytes)"
                    )
                for entry in data:
                    raw = entry.get("name_value", "")
                    for name in raw.split("\n"):
                        name = name.strip().lower()
                        if name.startswith("*."):
                            name = name[2:]
                        if name.endswith(f".{domain}") or name == domain:
                            subs.add(name)
                break
        except (requests.RequestException, ValueError):
            if attempt == 0:
                threading.Event().wait(3)
                continue
            raise
    return subs


def _query_certspotter(domain):
    """Fallback: query CertSpotter API for subdomains."""
    from cms.constants import HEADERS

    cs_url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names&after="
    cs_headers = {
        "Accept": "application/json",
        "User-Agent": HEADERS.get("User-Agent", "Mozilla/5.0"),
    }
    subs = set()
    try:
        cs_resp = jittered_get(cs_url, headers=cs_headers, timeout=30)
        if cs_resp.status_code == 200:
            try:
                cs_data = cs_resp.json()
            except json.JSONDecodeError:
                cs_data = []
            for entry in cs_data:
                for name in entry.get("dns_names", []):
                    name = name.strip().lower().lstrip("*.")
                    if name.endswith(f".{domain}") or name == domain:
                        subs.add(name)
    except requests.RequestException:
        logger.debug("Certspotter fallback failed for %s", domain, exc_info=True)
    return subs


def _process_subdomains(subs, domain, subject_id=None):
    """Deduplicate and format subdomain findings."""
    findings = []
    sorted_subs = sorted(subs)[:50]
    if not sorted_subs:
        findings.append(
            {
                "title": f"No subdomains found for {domain}",
                "detail": "No certificate transparency logs with subdomains were found.",
                "source_type": "subdomain",
                "icon": "🌐",
                "verified": False,
                "subject_id": subject_id,
            }
        )
        return findings

    findings.append(
        {
            "title": f"{len(sorted_subs)} subdomains found for {domain}",
            "detail": "\n".join(sorted_subs[:30])
            + ("\n... and more" if len(sorted_subs) > 30 else ""),
            "source_url": f"https://crt.sh/?q=%25.{domain}",
            "source_type": "subdomain",
            "icon": "🌐",
            "verified": True,
            "subject_id": subject_id,
            "screenshots": [
                {"url": None, "source_url": f"https://crt.sh/?q=%25.{domain}"}
            ],
        }
    )
    return findings


def _subdomain_check(action):
    """Check subdomains for a domain via crt.sh and CertSpotter."""
    domain = action.data_value if action.data_value else None
    subject = None
    if not domain:
        subject = _action_subject(action)
        if subject and subject.email and "@" in subject.email:
            domain = subject.email.split("@")[1]
    if not domain:
        return []

    subject_id = subject.id if subject else None
    if not subject_id:
        subject = _action_subject(action)
        subject_id = subject.id if subject else None

    try:
        subs = _query_crtsh(domain)
        if not subs:
            subs = _query_certspotter(domain)
        return _process_subdomains(subs, domain, subject_id=subject_id)
    except Exception as e:
        return [
            {
                "title": f"Subdomein scan error: {e}",
                "detail": str(e),
                "source_type": "subdomain",
                "icon": "🌐",
                "verified": False,
            }
        ]


def _photo_analysis(action):
    """Analyze photo EXIF metadata, GPS, camera info, and generate reverse search links.

    Photo source priority:
    1. ``action.data_value`` resolved through the canonical storage helper,
       scoped to ``action.tenant_id`` (current ad-hoc uploads stored under
       ``instance/photo_analysis/<tenant_id>/``).
    2. ``subject.photo_path`` translated via ``current_app.root_path`` (the
       existing permanent subject photo under ``static/uploads/subjects``).

    Historical ``data_value`` entries that point to the old ``cms/static``
    location resolve to None (fail-safe), the worker then falls back to the
    subject photo (if present) and otherwise emits a clean "photo unavailable"
    finding — never a 500. Transient uploads are deleted after analysis (not
    subject photos, which are permanent).
    """
    from flask import current_app

    from cms.services.photo_analysis import (
        analyze_photo,
        format_analysis_finding,
    )
    from cms.services.photo_storage import resolve_photo_path, unlink_photo

    subject = _action_subject(action)
    findings = []

    photo_path = None
    photo_url = None
    is_adhoc_upload = False

    # 1. Resolve ad-hoc upload via the canonical, tenant-scoped storage root.
    if action.data_value:
        resolved = resolve_photo_path(action.data_value, action.tenant_id)
        if resolved:
            photo_path = resolved
            is_adhoc_upload = True

    # 2. Fall back to the permanent subject photo (served by Flask /static).
    if not photo_path and subject and subject.photo_path:
        candidate = os.path.join(
            current_app.root_path,
            "static",
            subject.photo_path.lstrip("/"),
        )
        if os.path.isfile(candidate):
            photo_path = candidate
            photo_url = subject.photo_path

    if not photo_path or not os.path.isfile(photo_path):
        findings.append(
            {
                "title": "No photo available for analysis",
                "detail": "No photo was provided. Click Photo Analysis again and upload an image.",
                "source_type": "photo_analysis",
                "icon": "📷",
                "verified": False,
            }
        )
        return findings

    try:
        analysis = analyze_photo(photo_path, photo_url=photo_url)
        finding = format_analysis_finding(
            analysis, subject_name=subject.name if subject else ""
        )
        if subject:
            finding["subject_id"] = subject.id
        findings.append(finding)

        if subject:
            subject.photo_metadata = {
                "gps": analysis.get("gps"),
                "camera": analysis.get("camera"),
                "datetime": analysis.get("datetime"),
                "software": analysis.get("software"),
                "privacy": analysis.get("privacy"),
            }
            db.session.commit()

        # AI geolocation fallback (Picarta) — only if no GPS in EXIF. Picarta
        # failures are swallowed here (debug) and can never mask the cleanup
        # below, which runs in the finally regardless.
        if not findings[0].get("raw_data", {}).get("gps"):
            try:
                picarta_findings = _picarta_geolocate(photo_path, subject)
                findings.extend(picarta_findings)
            except Exception as e:
                logger.debug("Picarta geolocation failed: %s", e)
    finally:
        # Remove the transient ad-hoc upload (tenant-scoped; a tenant A action
        # can never delete a tenant B file). Subject photos (permanent) are
        # never touched. Cleanup is best-effort and swallowed, so it never
        # masks an analysis failure: any exception from analyze_photo /
        # format_analysis_finding / metadata-commit propagates to run_action,
        # which marks the action "error".
        if is_adhoc_upload:
            unlink_photo(action.data_value, action.tenant_id)

    return findings


def _picarta_geolocate(photo_path, subject=None):
    """Use Picarta API for AI-based photo geolocation (fallback when no EXIF GPS)."""
    from cms.services.http_utils import jittered_get

    findings = []
    try:
        import base64

        with open(photo_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # Try Picarta API (free tier: 50 requests/month)
        picarta_key = _get_api_key("picarta_api_key")
        if not picarta_key:
            return findings

        r = jittered_get(
            "https://api.picarta.ai/geo",
            params={"token": picarta_key},
            json={"image": img_b64},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            lat = data.get("lat")
            lng = data.get("lon") or data.get("lng")
            confidence = data.get("confidence", 0)
            if lat and lng:
                detail_lines = [
                    "🤖 AI Estimated Location (Picarta)",
                    f"📍 Coordinates: {lat}, {lng}",
                    f"🗺️  https://www.google.com/maps?q={lat},{lng}",
                    f"📊 Confidence: {confidence}%",
                    "",
                    "⚠️ This is an AI estimate, not exact GPS data.",
                ]
                finding = {
                    "title": f"AI Geolocation — {lat},{lng} (confidence: {confidence}%)",
                    "detail": "\n".join(detail_lines),
                    "source_type": "ai_geolocation",
                    "icon": "🤖",
                    "verified": False,
                    "raw_data": {
                        "lat": lat,
                        "lng": lng,
                        "confidence": confidence,
                        "source": "picarta",
                    },
                }
                if subject:
                    finding["subject_id"] = subject.id
                findings.append(finding)
    except Exception as e:
        logger.debug("Picarta geolocation failed: %s", e)

    return findings
