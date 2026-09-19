"""Security contracts for the optional local Brave/Chrome evidence extension."""

import json
from pathlib import Path

from cms.models import Case, FeatureFlag, Finding, User, db


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "browser_extensions" / "local_finding_capture"


def test_manifest_is_narrow_and_has_no_broad_web_access():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["activeTab", "storage", "tabs"]
    assert manifest["host_permissions"] == ["https://joost.iveras.com/*"]
    assert manifest["action"]["default_popup"] == "popup.html"
    assert "<all_urls>" not in json.dumps(manifest)


def test_extension_keeps_capture_in_session_and_uses_dashboard_page_upload_bridge():
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    content = (EXTENSION / "content.js").read_text(encoding="utf-8")
    bridge = (
        ROOT
        / "templates"
        / "cms"
        / "workflow"
        / "_local_browser_capture_bridge.html"
    ).read_text(encoding="utf-8")

    assert "chrome.storage.session" in background
    assert "chrome.storage.local" not in background
    assert "captureVisibleTab" in background
    assert "MAX_AGE_MS = 10 * 60 * 1000" in background
    assert "document.cookie" not in background
    assert "chrome.cookies" not in background
    assert 'type: "REQUEST_DASHBOARD_UPLOAD"' in background
    assert "OSINT_LOCAL_CAPTURE_UPLOAD" in content
    assert "OSINT_LOCAL_CAPTURE_UPLOAD_RESULT" in content
    assert "window.postMessage" in content
    assert 'credentials: "same-origin"' in bridge
    assert "local-browser-capture.jpg" in bridge
    assert "X-CSRFToken" in bridge


def test_extension_requires_two_explicit_user_steps_and_keeps_source_url():
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    content = (EXTENSION / "content.js").read_text(encoding="utf-8")

    assert 'type === "ARM_CAPTURE"' in background
    assert 'type === "CAPTURE_ACTIVE_TAB"' in background
    assert 'type === "FOCUS_DASHBOARD_UPLOAD"' in background
    assert 'type === "UPLOAD_PENDING"' in background
    assert "sourceUrl: tab.url" in background
    assert "source.href" in (
        ROOT / "templates" / "cms" / "workflow" / "_local_browser_capture_bridge.html"
    ).read_text(encoding="utf-8")
    assert 'data-browser-capture' in content
    assert 'Upload screenshot' in content
    assert 'Discard' in content
    assert 'SHOW_PENDING_UPLOAD' in content
    assert 'window.addEventListener("pageshow", refreshPendingUpload)' in content

    popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")
    assert 'type: "CAPTURE_ACTIVE_TAB"' in popup
    assert 'type: "FOCUS_DASHBOARD_UPLOAD"' in popup
    assert "Go to upload" in (EXTENSION / "popup.html").read_text(encoding="utf-8")
    assert "Capture visible tab" in (EXTENSION / "popup.html").read_text(
        encoding="utf-8"
    )


def test_dashboard_button_is_flagged_and_writer_only():
    policy = (ROOT / "cms" / "feature_flag_policy.py").read_text(encoding="utf-8")
    route = (ROOT / "cms" / "workflow" / "routes.py").read_text(encoding="utf-8")
    readonly_finding = (
        ROOT / "templates" / "cms" / "workflow" / "_finding_item_readonly.html"
    ).read_text(encoding="utf-8")
    regular_finding = (
        ROOT / "templates" / "cms" / "workflow" / "_finding_item.html"
    ).read_text(encoding="utf-8")

    assert '"local_browser_screenshot_capture"' in policy
    assert '"local_browser_screenshot_capture", current_user.tenant_id' in route
    assert "local_browser_capture_enabled" in route
    for template in (readonly_finding, regular_finding):
        assert "{% if local_browser_capture_enabled %}" in template
        assert "data-browser-capture" in template


def _case_and_finding(auth_client):
    response = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "Local capture client",
            "title": "Local browser capture case",
            "subject_0_name": "Local capture subject",
            "subject_0_type": "person",
            "priority": "medium",
        },
    )
    assert response.status_code in (200, 302)
    case = Case.query.filter_by(title="Local browser capture case").one()
    admin = User.query.filter_by(username="admin").one()
    finding = Finding(
        tenant_id=case.tenant_id,
        case_id=case.id,
        title="Local browser capture finding",
        content="Evidence",
        source_type="manual",
        status="candidate",
        created_by=admin.id,
    )
    db.session.add(finding)
    db.session.commit()
    return case, finding


def test_case_detail_shows_browser_capture_only_when_enabled(app, auth_client):
    case, finding = _case_and_finding(auth_client)
    url = f"/cms/workflow/case/{case.id}"

    off = auth_client.get(url).get_data(as_text=True)
    assert f'data-finding-id="{finding.id}"' in off
    assert "data-browser-capture" not in off
    assert "OSINT_LOCAL_CAPTURE_UPLOAD" not in off

    db.session.add(
        FeatureFlag(
            tenant_id=case.tenant_id,
            flag_name="local_browser_screenshot_capture",
            enabled=True,
        )
    )
    db.session.commit()
    on = auth_client.get(url).get_data(as_text=True)
    assert "data-browser-capture" in on
    assert f'data-case-id="{case.id}"' in on
    assert f'data-finding-id="{finding.id}"' in on
    assert "OSINT_LOCAL_CAPTURE_UPLOAD" in on


def test_dashboard_templates_include_authenticated_upload_bridge():
    bridge_include = '{% include "cms/workflow/_local_browser_capture_bridge.html" %}'
    for name in ("workflow_case_detail.html", "workflow_investigation_detail.html"):
        template = (ROOT / "templates" / "cms" / "workflow" / name).read_text(
            encoding="utf-8"
        )
        assert bridge_include in template
