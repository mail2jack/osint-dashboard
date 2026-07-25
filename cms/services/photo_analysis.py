"""Photo OSINT analysis service.

Extracts EXIF metadata, GPS coordinates, camera info, generates reverse
image search URLs, and performs privacy/OPSEC analysis.
"""

import logging
import os
from typing import Optional

from PIL.ExifTags import TAGS, GPSTAGS

logger = logging.getLogger(__name__)

# Known photo-editing software (for OPSEC detection)
_EDITING_SOFTWARE = {
    "photoshop",
    "lightroom",
    "gimp",
    "snapseed",
    "vsco",
    "afterlight",
    "facetune",
    "pixlr",
    "canva",
    "darktable",
    "capture one",
    "affinity photo",
    "sketchbook",
    "procreate",
    "photopea",
}

# Privacy-sensitive EXIF tags
_GPS_TAGS = {"GPSLatitude", "GPSLongitude", "GPSLatitudeRef", "GPSLongitudeRef"}
_AUTHOR_TAGS = {"Artist", "Copyright", "ImageDescription", "UserComment"}
_DEVICE_TAGS = {"Make", "Model", "LensMake", "LensModel", "Software", "HostComputer"}


def _convert_to_degrees(value):
    """Convert GPS coordinates from EXIF format (degrees, minutes, seconds)
    to decimal degrees."""
    try:
        if isinstance(value, (list, tuple)) and len(value) == 3:
            d = float(value[0])
            m = float(value[1])
            s = float(value[2])
            return d + (m / 60.0) + (s / 3600.0)
        return float(value)
    except (TypeError, ValueError, IndexError):
        return None


def _get_exif_data(image_path: str) -> dict:
    """Extract all EXIF data from an image file."""
    try:
        from PIL import Image

        img = Image.open(image_path)
        exif_data = {}
        raw_exif = img._getexif()
        if raw_exif:
            for tag_id, value in raw_exif.items():
                tag_name = TAGS.get(tag_id, str(tag_id))
                try:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    exif_data[tag_name] = value
                except Exception:
                    exif_data[tag_name] = str(value)
        return exif_data
    except Exception as e:
        logger.debug("EXIF extraction failed for %s: %s", image_path, e)
        return {}


def _get_gps_info(exif_data: dict) -> Optional[dict]:
    """Extract GPS coordinates from EXIF data."""
    try:
        gps_info = exif_data.get("GPSInfo")
        if not gps_info:
            return None

        decoded = {}
        for key, val in gps_info.items():
            tag = GPSTAGS.get(key, key)
            decoded[tag] = val

        lat = decoded.get("GPSLatitude")
        lat_ref = decoded.get("GPSLatitudeRef", "N")
        lng = decoded.get("GPSLongitude")
        lng_ref = decoded.get("GPSLongitudeRef", "E")

        if not lat or not lng:
            return None

        lat_deg = _convert_to_degrees(lat)
        lng_deg = _convert_to_degrees(lng)

        if lat_deg is None or lng_deg is None:
            return None

        if lat_ref == "S":
            lat_deg = -lat_deg
        if lng_ref == "W":
            lng_deg = -lng_deg

        result = {
            "latitude": round(lat_deg, 6),
            "longitude": round(lng_deg, 6),
            "google_maps_url": f"https://www.google.com/maps?q={lat_deg},{lng_deg}",
        }

        altitude = decoded.get("GPSAltitude")
        if altitude:
            try:
                result["altitude"] = float(altitude)
            except (TypeError, ValueError):
                pass

        return result
    except Exception as e:
        logger.debug("GPS extraction failed: %s", e)
        return None


def _get_camera_info(exif_data: dict) -> Optional[dict]:
    """Extract camera information from EXIF data."""
    camera = {}
    for tag in ("Make", "Model", "LensMake", "LensModel", "Software"):
        val = exif_data.get(tag)
        if val:
            camera[tag.lower()] = str(val).strip()

    if not camera:
        return None

    if "make" in camera and "model" in camera:
        if camera["model"].lower().startswith(camera["make"].lower()):
            camera["camera_name"] = camera["model"]
        else:
            camera["camera_name"] = f"{camera['make']} {camera['model']}"

    return camera


def _get_datetime(exif_data: dict) -> Optional[str]:
    """Extract the best datetime from EXIF data."""
    for tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        val = exif_data.get(tag)
        if val:
            return str(val)
    return None


def _get_software(exif_data: dict) -> Optional[str]:
    """Extract software used to create/edit the image."""
    return exif_data.get("Software")


def _has_thumbnail(exif_data: dict) -> bool:
    """Check if the image has an embedded thumbnail."""
    return "JPEGThumbnail" in exif_data or "TIFFThumbnail" in exif_data


def _privacy_analysis(exif_data: dict, gps_info: Optional[dict]) -> dict:
    """Analyze privacy/OPSEC risks in the image metadata."""
    risks = []
    info_items = []

    # GPS check
    if gps_info:
        risks.append(
            {
                "level": "high",
                "icon": "🔴",
                "message": "GPS location exposed — photo reveals where it was taken",
                "detail": f"Coordinates: {gps_info['latitude']}, {gps_info['longitude']}",
            }
        )
    else:
        info_items.append(
            {"level": "ok", "icon": "✅", "message": "No GPS coordinates found"}
        )

    # Camera/device identification
    camera = _get_camera_info(exif_data)
    if camera:
        risks.append(
            {
                "level": "medium",
                "icon": "🟡",
                "message": "Camera/device identifiable",
                "detail": camera.get("camera_name", ""),
            }
        )

    # Software (editing detection)
    software = _get_software(exif_data)
    if software:
        sw_lower = software.lower()
        is_editor = any(s in sw_lower for s in _EDITING_SOFTWARE)
        if is_editor:
            risks.append(
                {
                    "level": "low",
                    "icon": "🟡",
                    "message": "Photo was edited with known software",
                    "detail": software,
                }
            )
        else:
            info_items.append(
                {
                    "level": "info",
                    "icon": "ℹ️",
                    "message": f"Software: {software}",
                    "detail": software,
                }
            )

    # Author/copyright
    has_author = False
    for tag in _AUTHOR_TAGS:
        val = exif_data.get(tag)
        if val and str(val).strip():
            has_author = True
            risks.append(
                {
                    "level": "medium",
                    "icon": "🟡",
                    "message": f"Author/copyright metadata present ({tag})",
                    "detail": str(val)[:200],
                }
            )
            break
    if not has_author:
        info_items.append(
            {"level": "ok", "icon": "✅", "message": "No author/copyright metadata"}
        )

    # Thumbnail risk
    if _has_thumbnail(exif_data):
        risks.append(
            {
                "level": "low",
                "icon": "🟡",
                "message": "Embedded thumbnail present — original may be recoverable",
                "detail": "EXIF thumbnails can contain a smaller version of the original photo",
            }
        )

    # Overall risk score
    high = sum(1 for r in risks if r["level"] == "high")
    medium = sum(1 for r in risks if r["level"] == "medium")
    if high > 0:
        risk_level = "high"
    elif medium > 0:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level,
        "risks": risks,
        "info": info_items,
        "total_tags": len(exif_data),
    }


def generate_reverse_search_urls(photo_url: str = None, photo_path: str = None) -> dict:
    """Generate reverse image search URLs for a photo.

    If photo_url is provided, uses URL-based search.
    If photo_path is provided, uses file-based search (returns upload URLs).
    """
    urls = {}

    if photo_url:
        encoded = photo_url
        urls = {
            "google_lens": f"https://lens.google.com/uploadbyurl?url={encoded}",
            "yandex": f"https://yandex.com/images/search?rpt=imageview&url={encoded}",
            "bing": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{encoded}",
            "tineye": f"https://tineye.com/search?url={encoded}",
        }
    else:
        urls = {
            "google_lens": "https://lens.google.com/",
            "yandex": "https://yandex.com/images/",
            "bing": "https://www.bing.com/images/",
            "tineye": "https://tineye.com/",
        }

    return urls


def analyze_photo(
    image_path: str,
    photo_url: str = None,
) -> dict:
    """Full photo analysis: EXIF, GPS, camera, privacy, reverse search.

    Returns a dict with all analysis results.
    """
    result = {
        "exif": {},
        "gps": None,
        "camera": None,
        "datetime": None,
        "software": None,
        "privacy": None,
        "reverse_search": {},
        "has_exif": False,
    }

    if not image_path or not os.path.exists(image_path):
        return result

    # EXIF extraction
    exif_data = _get_exif_data(image_path)
    result["exif"] = exif_data
    result["has_exif"] = bool(exif_data)

    # GPS
    result["gps"] = _get_gps_info(exif_data)

    # Camera
    result["camera"] = _get_camera_info(exif_data)

    # DateTime
    result["datetime"] = _get_datetime(exif_data)

    # Software
    result["software"] = _get_software(exif_data)

    # Privacy analysis
    result["privacy"] = _privacy_analysis(exif_data, result["gps"])

    # Reverse search URLs
    result["reverse_search"] = generate_reverse_search_urls(photo_url=photo_url)

    return result


def format_analysis_finding(analysis: dict, subject_name: str = "") -> dict:
    """Format photo analysis results into a finding dict for the workflow."""
    lines = []

    # GPS
    gps = analysis.get("gps")
    if gps:
        lines.append(f"📍 GPS: {gps['latitude']}, {gps['longitude']}")
        lines.append(f"   🗺️  {gps.get('google_maps_url', '')}")
        if gps.get("altitude"):
            lines.append(f"   Altitude: {gps['altitude']}m")
    else:
        lines.append("📍 No GPS coordinates found")

    lines.append("")

    # Camera
    camera = analysis.get("camera")
    if camera:
        name = camera.get("camera_name", "Unknown")
        lines.append(f"📷 Camera: {name}")
        if camera.get("lensmodel"):
            lines.append(f"   Lens: {camera['lensmodel']}")
    else:
        lines.append("📷 No camera info available")

    # DateTime
    dt = analysis.get("datetime")
    if dt:
        lines.append(f"📅 Date/Time: {dt}")

    # Software
    sw = analysis.get("software")
    if sw:
        lines.append(f"💻 Software: {sw}")

    lines.append("")

    # Privacy
    privacy = analysis.get("privacy", {})
    risk_level = privacy.get("risk_level", "unknown")
    risk_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(risk_level, "⚪")
    lines.append(f"{risk_icon} Privacy Risk: {risk_level.upper()}")

    for risk in privacy.get("risks", []):
        lines.append(f"  {risk['icon']} {risk['message']}")
    for info in privacy.get("info", []):
        lines.append(f"  {info['icon']} {info['message']}")

    lines.append("")

    # Reverse search links
    rev = analysis.get("reverse_search", {})
    if rev:
        lines.append("🔍 Reverse Image Search:")
        for engine, url in rev.items():
            label = engine.replace("_", " ").title()
            lines.append(f"  {label}: {url}")

    detail = "\n".join(lines)

    # Build raw_data for structured access
    raw_data = {
        "gps": analysis.get("gps"),
        "camera": analysis.get("camera"),
        "datetime": analysis.get("datetime"),
        "software": analysis.get("software"),
        "privacy": analysis.get("privacy"),
        "reverse_search": analysis.get("reverse_search"),
        "exif_tag_count": len(analysis.get("exif", {})),
    }

    title_parts = ["Photo Analysis"]
    if subject_name:
        title_parts.append(subject_name)
    if gps:
        title_parts.append(f"GPS: {gps['latitude']},{gps['longitude']}")

    return {
        "title": " — ".join(title_parts),
        "detail": detail,
        "source_type": "photo_analysis",
        "icon": "📷",
        "verified": False,
        "raw_data": raw_data,
    }


def analyze_photo_from_bytes(
    image_bytes: bytes,
    photo_url: str = None,
) -> dict:
    """Analyze photo from raw bytes (e.g., uploaded file content)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        return analyze_photo(tmp_path, photo_url=photo_url)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
