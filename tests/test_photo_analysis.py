"""Tests for photo analysis service."""

import os
import tempfile

from PIL import Image

from cms.services.photo_analysis import (
    _convert_to_degrees,
    _get_exif_data,
    _get_gps_info,
    _get_camera_info,
    _privacy_analysis,
    generate_reverse_search_urls,
    analyze_photo,
    format_analysis_finding,
)


def _create_test_image(exif_gps=None, exif_camera=None, exif_software=None):
    """Create a test image with optional EXIF data."""
    img = Image.new("RGB", (100, 100), color="red")
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    img.save(tmp.name, "JPEG")

    if exif_gps or exif_camera or exif_software:
        from PIL import Image as PILImage

        from PIL.ExifTags import TAGS

        img2 = PILImage.open(tmp.name)
        exif = img2.getexif()

        if exif_camera:
            for tag, val in exif_camera.items():
                for tag_id, tag_name in TAGS.items():
                    if tag_name == tag:
                        exif[tag_id] = val

        if exif_software:
            for tag_id, tag_name in TAGS.items():
                if tag_name == "Software":
                    exif[tag_id] = exif_software

        if exif_gps:
            from PIL.ExifTags import GPS

            gps_ifd = {}
            for tag, val in exif_gps.items():
                for tag_id, tag_name in GPS.items():
                    if tag_name == tag:
                        gps_ifd[tag_id] = val
            exif[0x8825] = gps_ifd

        img2.save(tmp.name, "JPEG", exif=exif.tobytes())

    return tmp.name


class TestConvertToDegrees:
    def test_valid_tuple(self):
        result = _convert_to_degrees((52, 3, 59.8))
        assert abs(result - 52.066611) < 0.001

    def test_invalid_input(self):
        assert _convert_to_degrees(None) is None
        assert _convert_to_degrees("invalid") is None


class TestExifExtraction:
    def test_no_exif(self):
        path = _create_test_image()
        try:
            exif = _get_exif_data(path)
            assert isinstance(exif, dict)
        finally:
            os.unlink(path)

    def test_nonexistent_file(self):
        exif = _get_exif_data("/nonexistent/file.jpg")
        assert exif == {}


class TestGpsInfo:
    def test_no_gps(self):
        path = _create_test_image()
        try:
            exif = _get_exif_data(path)
            gps = _get_gps_info(exif)
            assert gps is None
        finally:
            os.unlink(path)


class TestCameraInfo:
    def test_no_camera(self):
        path = _create_test_image()
        try:
            exif = _get_exif_data(path)
            camera = _get_camera_info(exif)
            assert camera is None
        finally:
            os.unlink(path)


class TestPrivacyAnalysis:
    def test_no_metadata(self):
        privacy = _privacy_analysis({}, None)
        assert privacy["risk_level"] == "low"
        assert len(privacy["risks"]) == 0

    def test_gps_risk(self):
        privacy = _privacy_analysis({}, {"latitude": 52.0, "longitude": 4.3})
        assert privacy["risk_level"] == "high"
        assert any("GPS" in r["message"] for r in privacy["risks"])

    def test_camera_risk(self):
        privacy = _privacy_analysis({"Make": "Apple", "Model": "iPhone 15"}, None)
        assert privacy["risk_level"] == "medium"

    def test_author_risk(self):
        privacy = _privacy_analysis({"Artist": "John Doe"}, None)
        assert privacy["risk_level"] == "medium"

    def test_photoshop_detected(self):
        privacy = _privacy_analysis({"Software": "Adobe Photoshop 24.0"}, None)
        assert any("edited" in r["message"].lower() for r in privacy["risks"])


class TestReverseSearchUrls:
    def test_with_url(self):
        urls = generate_reverse_search_urls(photo_url="https://example.com/photo.jpg")
        assert "google_lens" in urls
        assert "yandex" in urls
        assert "tineye" in urls
        assert "bing" in urls
        assert "example.com" in urls["google_lens"]

    def test_without_url(self):
        urls = generate_reverse_search_urls()
        assert "google_lens" in urls


class TestAnalyzePhoto:
    def test_nonexistent_file(self):
        result = analyze_photo("/nonexistent/file.jpg")
        assert result["has_exif"] is False
        assert result["gps"] is None

    def test_basic_image(self):
        path = _create_test_image()
        try:
            result = analyze_photo(path)
            assert result["has_exif"] is False
            assert result["gps"] is None
            assert result["camera"] is None
            assert result["privacy"]["risk_level"] == "low"
        finally:
            os.unlink(path)


class TestFormatFinding:
    def test_basic_finding(self):
        analysis = {
            "gps": None,
            "camera": None,
            "datetime": None,
            "software": None,
            "privacy": {"risk_level": "low", "risks": [], "info": []},
            "reverse_search": {"google_lens": "https://lens.google.com/"},
        }
        finding = format_analysis_finding(analysis, subject_name="Test Subject")
        assert "Photo Analysis" in finding["title"]
        assert "Test Subject" in finding["title"]
        assert finding["source_type"] == "photo_analysis"
        assert finding["icon"] == "📷"
        assert "google_lens" in finding["raw_data"]["reverse_search"]

    def test_finding_with_gps(self):
        analysis = {
            "gps": {
                "latitude": 52.0,
                "longitude": 4.3,
                "google_maps_url": "https://maps.google.com/?q=52,4.3",
            },
            "camera": {"camera_name": "iPhone 15 Pro"},
            "datetime": "2026:07:25 14:23:00",
            "software": "17.5.1",
            "privacy": {
                "risk_level": "high",
                "risks": [{"level": "high", "icon": "🔴", "message": "GPS exposed"}],
                "info": [],
            },
            "reverse_search": {},
        }
        finding = format_analysis_finding(analysis, subject_name="John")
        assert "GPS: 52.0,4.3" in finding["title"]
        assert "52.0" in finding["detail"]
        assert "Google Maps" in finding["detail"] or "maps" in finding["detail"].lower()
