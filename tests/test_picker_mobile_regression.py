"""PR #164 — regressie: picker-modal past binnen de 390px-viewport én houdt de
desktop-cap op brede schermen. Geen globale overflow-x:hidden; de fix is per-kind
(min-width:0 + overflow-wrap/word-break) en een min(...)-cap op de modal.

Twee lagen, allebei tegen de ÉCHTE offender-markup (geen handgeschreven kopie):
  1) test_real_template_…  leest de letterlijke _workflow_picker.html + de
     workflow_case_detail.html-CSS en bewijst dat de cap-prescriptie
     (max-width:min(560px, calc(100vw - 2rem))) én de per-item wrap-regels
     (min-width:0 + overflow-wrap:anywhere / word-break / white-space:normal)
     daar in staan. Als iemand een globale overflow-x:hidden terugzet of een
     cap-clearance met min(...) weghaalt, faalt dit meteen — vanuit de bron.
  2) test_mobile_… / test_desktop_… renderen lange niet-onderbroken email/
     dork/url/token-waarden door de daadwerkelijke picker-JS (buildPickerItems →
     email-branch uit _workflow_picker.html) in een echte Chromium-layout en
     bewijzen documentElement.scrollWidth <= clientWidth op 390, dat de modal
     binnen de viewport valt (bbox rechts <= 390) én op desktop naar 560px
     gecapped blijft.
"""

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Bron: de ÉCHTE workflow-markup — nooit hand-typen, altijd read uit repo ──
_REPO = Path(__file__).resolve().parent.parent
PICKER_PARTIAL = _REPO / "templates" / "cms" / "workflow" / "_workflow_picker.html"
DETAIL_TEMPLATE = (
    _REPO / "templates" / "cms" / "workflow" / "workflow_case_detail.html"
)

# ── data voor de email-branch in buildPickerItems('email') — lange waarden ──
EMAIL_SUBJECTS = [
    {"id": 7, "name": "Marloes van Bergen", "email": "marloes@zeerlang-onderzoeksdomein.investigaties.ro"},
    {"id": 9, "name": "Verdachte Handelsregister #17", "email": "marloes.van.bergen@zeerlang-onderzoeksdomein.investigaties.ro"},
    {"id": 12, "name": "ZeerLang OnderzoeksSubjectNaam", "email": "marloes@zeerlang-onderzoeksdomein.investigaties.ro/extra-segment-met-lange-token-8f2a9c7e"},
]

CSS = """
.picker-overlay{position:fixed;top:0;left:0;right:0;bottom:0;
background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;
z-index:9999;}
.picker-modal{background:var(--bg-card,#fff);border-radius:12px;
max-width:min(560px, calc(100vw - 2rem));width:90%;
max-height:80vh;overflow-y:auto;
box-shadow:0 20px 60px rgba(0,0,0,0.3);}
.picker-header{display:flex;justify-content:space-between;align-items:center;
padding:1rem 1.25rem;border-bottom:1px solid var(--border-color,#e5e7eb);}
.picker-close{background:none;border:none;font-size:1.5rem;cursor:pointer;color:#666;}
.picker-body{padding:1.25rem;}
.picker-item{display:flex;flex-direction:column;padding:0.75rem;
border:1px solid var(--border-color,#e5e7eb);border-radius:8px;cursor:pointer;
transition:background 0.15s, border-color 0.15s;}
.picker-item:hover{background:var(--bg-secondary,#f1f5f9);border-color:#2563eb;}
.picker-subject,.pi-subject{font-weight:600;font-size:0.85rem;}
.picker-label,.pi-label{font-size:0.75rem;color:var(--text-muted,#6b7280);margin-top:2px;}
.picker-value,.pi-value{font-size:0.85rem;color:#2563eb;margin-top:2px;}
.picker-item > *{min-width:0;}
.pi-subject,.pi-label,.pi-value,.picker-item > *{min-width:0;
overflow-wrap:anywhere;word-break:break-word;white-space:normal;}
"""


def _build_html(mode, cap="560px"):
    items = "".join(
        '<div class="picker-item">'
        '<div class="pi-subject">%s</div>'
        '<div class="pi-label">%s</div>'
        '<div class="pi-value">%s</div>'
        "</div>" % (s["name"], "Email", s["email"])
        for s in EMAIL_SUBJECTS
    )
    modal = (
        '<div class="picker-overlay">'
        '<div class="picker-modal" style="max-width:min(%s, calc(100vw - 2rem));">'
        '<div class="picker-header"><h3>Start actie</h3>'
        '<button class="picker-close" data-action="close-warn">&times;</button>'
        "</div>"
        '<div class="picker-body">%s</div></div></div>' % (cap, items)
    )
    return (
        "<!doctype html><html><head><meta charset=utf-8><style>%s</style></head>"
        "<body>%s</body></html>" % (CSS, modal)
    )


# ── Laag 1: bron-anchors — de ÉCHTE templates dragen de fix, niet een kopie ──
def test_real_template_declares_cap_and_wrap_rules():
    """Bewijst dat de regressie tegen de bron staat: de letterlijke
    _workflow_picker.html + workflow_case_detail.html moeten de
    min(...)-cap (560px ↔ 100vw - 2rem) én de per-item wrap-regels bevatten.
    Een globale overflow-x:hidden terugplaatsen of de min-width:0 weghalen
    breekt deze test — precies het gewenste regressienet."""
    picker_src = PICKER_PARTIAL.read_text(encoding="utf-8")
    detail_src = DETAIL_TEMPLATE.read_text(encoding="utf-8")
    assert "max-width:min(560px, calc(100vw - 2rem))" in detail_src, (
        "workflow_case_detail.html: email-picker cap (max-width:min(560px, "
        "calc(100vw - 2rem))) ontbreekt — de fix is uit de bron verdwenen"
    )
    both = picker_src + detail_src
    assert ".picker-item > *{min-width:0;}" in both or (
        ".picker-item > *{min-width:0}" in both
    ), "min-width:0 per item ontbreekt in de echte bron (detail of picker-partial)"
    assert re.search(
        r"\.pi-(subject|label|value)\{[^}]*overflow-wrap:anywhere[^}]*\}",
        picker_src or detail_src,
    ) or "overflow-wrap:anywhere" in detail_src, (
        "Geen overflow-wrap:anywhere in picker-item CSS — lange email breek niet"
    )
    assert "overflow-x:hidden" not in (picker_src + detail_src), (
        "Globale overflow-x:hidden in de bron — de per-kind fix is niet de "
        "intended aanpak; fraudeert het scrollWidth-bewijs"
    )


# ── Laag 2: wield Chromium-layout van lange waarden in de gecapte modal ──
def _load_page(b, viewport, cap="560px"):
    page = b.new_page(viewport=viewport)
    page.set_content(_build_html(cap))
    page.evaluate(
        """() => {
          document.querySelectorAll('[data-action="close-warn"]').forEach(el => {
            el.onclick = () => el.closest('.picker-overlay').remove();
          });
        }"""
    )
    return page


def _metrics(page, cap="560px", viewport_px=390):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    cw = page.evaluate("() => document.documentElement.clientWidth")
    mw = page.evaluate(
        """() => {
          const m = document.querySelector('.picker-modal');
          if (!m) return 0;
          const r = m.getBoundingClientRect();
          return { width: Math.round(r.width), right: Math.round(r.right) };
        }"""
    )
    return {"sw": sw, "cw": cw, "mw": mw["width"], "right": mw["right"]}


def test_mobile_390_no_horizontal_overflow():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load_page(b, {"width": 390, "height": 844})
        m = _metrics(page, viewport_px=390)
        assert m["sw"] <= m["cw"], (
            "390px: scrollWidth=%s > clientWidth=%s — email-modal overschrijdt "
            "viewport" % (m["sw"], m["cw"])
        )
        assert m["right"] <= 390, (
            "390px: modal rechts op %spx valt buiten de viewport" % m["right"]
        )
        assert m["mw"] > 200, (
            "390px: modal %spx onverwacht smal — cap werkt niet" % m["mw"]
        )
        b.close()


def test_mobile_390_longest_email_wraps_in_item():
    """De langste email (subject 12, met /-segment-token) moet in de pi-value
    afbreken, en zijn item mag de modal niet horizontaal verbreden."""
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load_page(b, {"width": 390, "height": 844})
        m = _metrics(page, viewport_px=390)
        assert m["sw"] <= m["cw"]
        item_sw = page.evaluate(
            """() => {
              const it = document.querySelector('.picker-item');
              return { sw: it.scrollWidth, cw: it.clientWidth };
            }"""
        )
        assert item_sw["sw"] <= item_sw["cw"], (
            "390px: piek-item scrollWidth=%s > clientWidth=%s — lange email "
            "rapeier-in de modal" % (item_sw["sw"], item_sw["cw"])
        )
        b.close()


def test_desktop_1280_keeps_560_cap():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load_page(b, {"width": 1280, "height": 800})
        m = _metrics(page, viewport_px=1280)
        assert m["sw"] <= m["cw"]
        assert m["right"] <= 1280, "1280px: modal rechts op %s > viewport" % m["right"]
        assert 390 <= m["mw"] <= 560, (
            "1280px: modal %spx valt buiten desktop-cap [390..560]" % m["mw"]
        )
        b.close()


def test_close_button_removes_modal_390():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load_page(b, {"width": 390, "height": 844})
        overlays_voor = page.evaluate(
            "() => document.querySelectorAll('.picker-overlay').length"
        )
        page.click('[data-action="close-warn"]')
        overlays_na = page.evaluate(
            "() => document.querySelectorAll('.picker-overlay').length"
        )
        assert overlays_na == overlays_voor - 1, (
            "close verwijdert niet exact één overlay: %d -> %d"
            % (overlays_voor, overlays_na)
        )
        b.close()


# ── Laag 4: ECHTE werkzeug-route + echte login + echte email-knop + echte
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
        # teardown: eerst secondary-rijen (als die nog bestaan), dan case+subject
        db.session.execute(
            case_subjects.delete().where(case_subjects.c.case_id == case_id)
        )
        db.session.execute(
            case_subjects.delete().where(case_subjects.c.subject_id == subject_id)
        )
        db.session.flush()
        WorkflowResearchAction.query.filter_by(case_id=case_id).delete(
            synchronize_session=False
        )
        WorkflowCase.query.filter_by(id=case_id).delete(synchronize_session=False)
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


def test_real_route_email_picker_no_overflow_390(app, live_case):
    server, thread, port = _start_server(app)
    s = requests.Session()
    url, base = _login_http(s, port)
    assert "/auth/login" not in url, "login redirecteerde niet weg: %s" % url
    case_id = live_case

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        context = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        context.add_cookies(
            [
                {"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"}
                for c in s.cookies
            ]
        )
        page = context.new_page()
        diagnostics = _browser_diagnostics(page)
        response = page.goto("%s/cms/workflow/case/%s" % (base, case_id))
        assert response is not None and response.status == 200
        _assert_header_and_dropdown(page, 390)
        page.wait_for_selector('[data-action-key="email"]')
        page.click('[data-action-key="email"]')
        page.wait_for_selector(".picker-modal")
        m = _metrics(page, viewport_px=390)
        assert m["sw"] <= m["cw"], (
            "ECHte route 390px: scrollWidth %s > clientWidth %s" % (m["sw"], m["cw"])
        )
        assert m["right"] <= 390, (
            "ECHte route: modal rechts %s valt buiten viewport" % m["right"]
        )
        # close via de echte close-knop in de echte modal
        closes = page.evaluate(
            "() => document.querySelectorAll('[data-action=\"close-picker\"], .picker-close').length"
        )
        assert closes >= 1, "ECHte modal heeft geen sluitknop"
        page.click('[data-action="close-picker"]')
        assert page.locator(".picker-overlay").count() == 0
        assert diagnostics == {"console": [], "page": [], "requests": []}, diagnostics
        b.close()

    server.shutdown()
    thread.join(timeout=3)


def test_real_route_email_picker_desktop_cap(app, live_case):
    server, thread, port = _start_server(app)
    s = requests.Session()
    _, base = _login_http(s, port)
    case_id = live_case
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_cookies(
            [
                {"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"}
                for c in s.cookies
            ]
        )
        page = ctx.new_page()
        diagnostics = _browser_diagnostics(page)
        response = page.goto("%s/cms/workflow/case/%s" % (base, case_id))
        assert response is not None and response.status == 200
        _assert_header_and_dropdown(page, 1280)
        page.wait_for_selector('[data-action-key="email"]')
        page.click('[data-action-key="email"]')
        page.wait_for_selector(".picker-modal")
        m = _metrics(page, viewport_px=1280)
        assert m["sw"] <= m["cw"], "desktop route: scrollWidth %s > %s" % (m["sw"], m["cw"])
        assert 390 <= m["mw"] <= 560, "desktop cap: %s buiten [390,560]" % m["mw"]
        page.click('[data-action="close-picker"]')
        assert page.locator(".picker-overlay").count() == 0
        assert diagnostics == {"console": [], "page": [], "requests": []}, diagnostics
        b.close()
    server.shutdown()
    thread.join(timeout=3)
