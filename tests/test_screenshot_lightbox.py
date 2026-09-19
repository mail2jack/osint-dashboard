"""Regression contracts for the in-dashboard finding screenshot viewer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "templates" / "cms" / "workflow"


def test_screenshot_links_open_the_dashboard_lightbox_not_a_raw_tab():
    for name in (
        "_finding_item.html",
        "_finding_item_readonly.html",
        "_workflow_polling.html",
        "workflow_findings.html",
    ):
        source = (WORKFLOW / name).read_text(encoding="utf-8")
        assert 'data-action="view-screenshot"' in source
        assert 'target="_blank" rel="noopener"><img' not in source


def test_lightbox_accepts_only_dashboard_upload_urls_and_is_dismissible():
    source = (WORKFLOW / "_finding_action_handlers.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "static" / "css" / "base.css").read_text(encoding="utf-8")

    assert "openScreenshotLightbox" in source
    assert "url.origin !== window.location.origin" in source
    assert "!url.pathname.startsWith('/cms/workflow/uploads/')" in source
    assert "event.key === 'Escape'" in source
    assert "screenshot-lightbox" in css
    assert "max-height: 100%" in css
