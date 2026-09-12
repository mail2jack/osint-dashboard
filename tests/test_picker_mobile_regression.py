"""PR #164 regression coverage for the production picker route and CSS."""

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Bron: de ÉCHTE workflow-markup — nooit hand-typen, altijd read uit repo ──
_REPO = Path(__file__).resolve().parent.parent
PICKER_PARTIAL = _REPO / "templates" / "cms" / "workflow" / "_workflow_picker.html"
DETAIL_TEMPLATE = (
    _REPO / "templates" / "cms" / "workflow" / "workflow_case_detail.html"
)
BASE_CSS = _REPO / "static" / "css" / "base.css"


# ── Bronankers: uitsluitend productiebronnen ──
def test_real_template_declares_cap_and_wrap_rules():
    """Lock the production picker caps, wrapping and scoped overflow policy."""
    picker_src = PICKER_PARTIAL.read_text(encoding="utf-8")
    detail_src = DETAIL_TEMPLATE.read_text(encoding="utf-8")
    base_src = BASE_CSS.read_text(encoding="utf-8")
    for cap in (
        "max-width:min(400px, calc(100vw - 2rem))",
        "max-width:min(640px, calc(100vw - 2rem))",
        "max-width:min(500px, calc(100vw - 2rem))",
        "max-width:min(480px, calc(100vw - 2rem))",
    ):
        assert cap in picker_src, "picker-cap ontbreekt uit productie-JS: %s" % cap
    assert "max-width:min(560px, calc(100vw - 2rem))" in detail_src
    assert re.search(
        r"\.picker-modal\s*\{[^}]*box-sizing\s*:\s*border-box[^}]*"
        r"min-width\s*:\s*0",
        detail_src,
        re.DOTALL,
    )
    assert re.search(
        r"\.picker-body\s*\{[^}]*min-width\s*:\s*0", detail_src, re.DOTALL
    )
    assert re.search(
        r"\.picker-item\s*\{[^}]*min-width\s*:\s*0", detail_src, re.DOTALL
    )
    assert re.search(
        r"\.picker-item\s*>\s*\*\s*\{[^}]*min-width\s*:\s*0",
        detail_src,
        re.DOTALL,
    )
    assert re.search(
        r"\.pi-subject\s*,\s*\.pi-label\s*,\s*\.pi-value\s*\{"
        r"[^}]*overflow-wrap\s*:\s*anywhere[^}]*"
        r"word-break\s*:\s*break-word[^}]*white-space\s*:\s*normal",
        detail_src,
        re.DOTALL,
    )
    global_clip = re.compile(
        r"(?:^|})\s*(?:html\s*,\s*body|body\s*,\s*html|html|body)\s*"
        r"\{[^}]*overflow-x\s*:\s*hidden\b",
        re.IGNORECASE | re.DOTALL,
    )
    assert not global_clip.search(picker_src + detail_src + base_src), (
        "Globale html/body overflow-x:hidden is niet toegestaan; gebruik lokale "
        "component-beperkingen."
    )


# ── Echte route: echte app, login, markup, JavaScript en CSS ──
#    picker-JS in echte Chromium (reviewer-gate: geen set_content voor deze).
#    Boot de échte app op een stack-lokale poort, seedt één zaak met een
#    subject (lange email) via ORM, logt echt in over HTTP-en-route en klikt
#    de échte data-action-key="email"-knop op de échte detail-URL; daarna draait
#    de echte _workflow_picker.html-JS (showActionPicker→buildPickerItems) en
#    bewijzen we overflow/clip/close in een echte Chromium-layout.
import socket
import threading
from datetime import date

import requests
import pytest
from werkzeug.serving import make_server

@pytest.fixture
def live_case(app):
    """Maakt in het échte app-db een zaak + subject met een zeer lange email."""
    from cms.models import db, Client, User, case_subjects
    from cms.workflow.routes import WorkflowCase, WorkflowSubject, WorkflowResearchAction

    with app.app_context():
        admin = User.query.filter_by(role="admin").first()
        tenant_id = admin.tenant_id if admin else 1
        original_totp_secret = admin.totp_secret
        original_totp_enabled = admin.totp_enabled
        original_is_super_admin = admin.is_super_admin
        case_id = None
        subject_id = None
        try:
            admin.totp_secret = "JBSWY3DPEHPK3PXP"
            admin.totp_enabled = True
            admin.is_super_admin = False
            db.session.commit()
            client = Client.query.filter_by(tenant_id=tenant_id).first()
            if client is None:
                client = Client(tenant_id=tenant_id, name="Test Client E2E")
                db.session.add(client)
                db.session.commit()
            case = WorkflowCase(
                tenant_id=tenant_id,
                case_number="E2E-090001",
                client_id=client.id,
                title="E2E case",
                priority="high",
                status="open",
                start_date=date(2026, 1, 1),
            )
            db.session.add(case)
            db.session.flush()
            subj = WorkflowSubject(
                tenant_id=tenant_id,
                subject_type="person",
                name="Marloes E2E",
                voornamen="Marloes",
                achternaam="LangOnderzoekE2E",
                email="marloes@zeerlang-onderzoeksdomein.investigaties.ro-extra-segment-met-lange-token-e2e-8f2a9c7e",
            )
            db.session.add(subj)
            db.session.flush()
            subject_id = subj.id
            case.subjects.append(subj)
            db.session.flush()
            db.session.add(
                WorkflowResearchAction(
                    tenant_id=tenant_id,
                    case_id=case.id,
                    subject_id=subj.id,
                    action_type="email",
                    data_value=subj.email,
                    label="Email",
                    status="ready",
                    target_kind="subject",
                )
            )
            db.session.commit()
            case_id = str(case.id)
            yield case_id
        finally:
            db.session.rollback()
            if case_id:
                db.session.execute(
                    case_subjects.delete().where(case_subjects.c.case_id == case_id)
                )
                WorkflowResearchAction.query.filter_by(case_id=case_id).delete(
                    synchronize_session=False
                )
                WorkflowCase.query.filter_by(id=case_id).delete(
                    synchronize_session=False
                )
            if subject_id:
                db.session.execute(
                    case_subjects.delete().where(
                        case_subjects.c.subject_id == subject_id
                    )
                )
                WorkflowSubject.query.filter_by(id=subject_id).delete(
                    synchronize_session=False
                )
            admin = User.query.filter_by(role="admin").first()
            admin.totp_secret = original_totp_secret
            admin.totp_enabled = original_totp_enabled
            admin.is_super_admin = original_is_super_admin
            db.session.commit()

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(app):
    port = _free_port()
    server = make_server("127.0.0.1", port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


def _login_http(session, port):
    base = "http://127.0.0.1:%d" % port
    session.get(base + "/auth/login")
    resp = session.post(
        base + "/auth/login",
        data={"email": "admin@localhost", "password": "Test1234!"},
        allow_redirects=False,
    )
    location = resp.headers.get("Location", "")
    if location.endswith("/auth/2fa/setup"):
        setup = session.get(base + location)
        match = re.search(r"<code>([A-Z2-7]+)</code>", setup.text)
        assert match, "2FA setup secret ontbreekt in de loginrespons"
        import pyotp

        setup = session.post(
            base + location,
            data={"code": pyotp.TOTP(match.group(1)).now()},
            allow_redirects=True,
        )
        return setup.url, base
    if location.endswith("/auth/2fa/verify"):
        session.get(base + location)
        import pyotp

        verify = session.post(
            base + location,
            data={"code": pyotp.TOTP("JBSWY3DPEHPK3PXP").now()},
            allow_redirects=True,
        )
        return verify.url, base
    return session.get(base + location).url if location else resp.url, base


def _browser_diagnostics(page):
    diagnostics = {"console": [], "page": [], "requests": []}
    page.on(
        "console",
        lambda message: diagnostics["console"].append(message.text)
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: diagnostics["page"].append(str(error)))
    page.on(
        "requestfailed",
        lambda request: diagnostics["requests"].append(
            "%s: %s" % (request.url, request.failure)
        ),
    )
    return diagnostics


def _assert_header_and_dropdown(page, viewport_width):
    header = page.locator(".header")
    box = header.bounding_box()
    assert box is not None, "header ontbreekt op echte case-detailpagina"
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport_width, (
        "header valt buiten viewport: %s bij %spx" % (box, viewport_width)
    )
    trigger = page.locator(".nav-dropdown-trigger").first
    assert trigger.is_visible(), "eerste navigatiedropdown is niet zichtbaar"
    trigger.click()
    assert trigger.get_attribute("aria-expanded") == "true"
    menu = page.locator(".nav-dropdown").first.locator(".nav-dropdown-menu")
    assert menu.is_visible(), "navigatiedropdown opent niet via click"
    trigger.click()
    assert trigger.get_attribute("aria-expanded") == "false"


def _metrics(page):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    cw = page.evaluate("() => document.documentElement.clientWidth")
    modal = page.locator(".picker-modal")
    box = modal.bounding_box()
    assert box is not None, "picker-modal ontbreekt"
    return {
        "sw": sw,
        "cw": cw,
        "mw": round(box["width"]),
        "right": round(box["x"] + box["width"]),
    }


def test_real_route_email_picker_no_overflow_390(app, live_case):
    server, thread, port = _start_server(app)
    try:
        s = requests.Session()
        url, base = _login_http(s, port)
        assert "/auth/login" not in url, "login redirecteerde niet weg: %s" % url
        case_id = live_case
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            try:
                context = b.new_context(
                    viewport={"width": 390, "height": 844}, has_touch=True
                )
                try:
                    context.add_cookies(
                        [
                            {
                                "name": c.name,
                                "value": c.value,
                                "domain": "127.0.0.1",
                                "path": "/",
                            }
                            for c in s.cookies
                        ]
                    )
                    page = context.new_page()
                    diagnostics = _browser_diagnostics(page)
                    response = page.goto(
                        "%s/cms/workflow/case/%s" % (base, case_id)
                    )
                    assert response is not None and response.status == 200
                    _assert_header_and_dropdown(page, 390)
                    page.wait_for_selector('[data-action-key="email"]')
                    page.click('[data-action-key="email"]')
                    page.wait_for_selector(".picker-modal")
                    m = _metrics(page)
                    assert m["sw"] <= m["cw"], (
                        "ECHte route 390px: scrollWidth %s > clientWidth %s"
                        % (m["sw"], m["cw"])
                    )
                    assert m["right"] <= 390, (
                        "ECHte route: modal rechts %s valt buiten viewport" % m["right"]
                    )
                    modal_box = page.locator(".picker-modal").bounding_box()
                    item_box = page.locator(".picker-item").last.bounding_box()
                    value_box = page.locator(".pi-value").last.bounding_box()
                    assert modal_box and item_box and value_box
                    modal_right = modal_box["x"] + modal_box["width"]
                    modal_bottom = modal_box["y"] + modal_box["height"]
                    assert item_box["x"] >= modal_box["x"]
                    assert item_box["x"] + item_box["width"] <= modal_right
                    assert item_box["y"] + item_box["height"] <= modal_bottom
                    assert value_box["x"] + value_box["width"] <= modal_right
                    assert value_box["y"] + value_box["height"] <= modal_bottom
                    assert page.locator('[data-action="close-picker"]').count() >= 1
                    page.click('[data-action="close-picker"]')
                    assert page.locator(".picker-overlay").count() == 0
                    assert diagnostics == {
                        "console": [],
                        "page": [],
                        "requests": [],
                    }, diagnostics
                finally:
                    context.close()
            finally:
                b.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_real_route_email_picker_desktop_cap(app, live_case):
    server, thread, port = _start_server(app)
    try:
        s = requests.Session()
        _, base = _login_http(s, port)
        case_id = live_case
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            try:
                context = b.new_context(viewport={"width": 1280, "height": 900})
                try:
                    context.add_cookies(
                        [
                            {
                                "name": c.name,
                                "value": c.value,
                                "domain": "127.0.0.1",
                                "path": "/",
                            }
                            for c in s.cookies
                        ]
                    )
                    page = context.new_page()
                    diagnostics = _browser_diagnostics(page)
                    response = page.goto(
                        "%s/cms/workflow/case/%s" % (base, case_id)
                    )
                    assert response is not None and response.status == 200
                    _assert_header_and_dropdown(page, 1280)
                    page.wait_for_selector('[data-action-key="email"]')
                    page.click('[data-action-key="email"]')
                    page.wait_for_selector(".picker-modal")
                    m = _metrics(page)
                    assert m["sw"] <= m["cw"], (
                        "desktop route: scrollWidth %s > %s" % (m["sw"], m["cw"])
                    )
                    assert 390 <= m["mw"] <= 560, (
                        "desktop cap: %s buiten [390,560]" % m["mw"]
                    )
                    page.click('[data-action="close-picker"]')
                    assert page.locator(".picker-overlay").count() == 0
                    assert diagnostics == {
                        "console": [],
                        "page": [],
                        "requests": [],
                    }, diagnostics
                finally:
                    context.close()
            finally:
                b.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
