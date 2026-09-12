"""PR #164 — regressie: picker-modal past binnen de 390px-viewport én houdt de
desktop-cap (560px) op brede schermen. Geen globale overflow-x:hidden; de fix is
per-kind (min-width:0 + overflow-wrap/word-break) en een min(...)-cap op de modal.

Rendert de letterlijke offender-markup uit _workflow_picker.html met lange
niet-onderbroken email/dork/url/token-waarden in een echte Chromium-layout en
bewijst documentElement.scrollWidth <= clientWidth op 390, én dat de modal
binnen de viewport valt (bbox rechts <= 390) en op desktop naar 560px capped blijft.
"""

from playwright.sync_api import sync_playwright

LONG_ITEMS = [
    ("marloes.van.bergen@zeerlang-onderzoeksdomein.investigaties.ro",
     "email.subject",
     "marloes@zeerlang-onderzoeksdomein.investigaties.ro"),
    ("Verdachte Handelsregister #17 / HandelsZaak Barendrecht / Aandeelhouder",
     "dork.lead",
     'site:kvk.nl inurl:"openbaar-register" "handelsregister" "bestuurder"'),
    ("8f2a9c7e-1b4d-4c3e-9a21-0e5d8664cf3c-zeerlang-uniek-token",
     "url.document",
     "https://storage.onderzoek.domein/documenten/"
     "8f2a9c7e-1b4d-4c3e-9a21-0e5d8664cf3c/hessen-lange-naam.pdf"),
    ("ZeerLang OnderzoeksSubjectNaam-DieNietMagAfbrekenInDeModalRij",
     "subject.fullname",
     "ZeerLang OnderzoeksSubjectNaam-DieNietMagAfbrekenInDeModalRij 1234567890"),
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
.picker-item{display:flex;flex-direction:column;padding:0.75rem;margin-bottom:0.5rem;
border:1px solid var(--border-color,#e5e7eb);border-radius:8px;cursor:pointer;
transition:background 0.15s, border-color 0.15s;}
.picker-item:hover{background:var(--bg-secondary,#f1f5f9);border-color:#2563eb;}
.picker-subject{font-weight:600;font-size:0.85rem;}
.picker-field{font-size:0.75rem;color:var(--text-muted,#6b7280);margin-top:2px;}
.picker-value{font-size:0.85rem;color:#2563eb;margin-top:2px;word-break:break-all;}
.picker-item > *{min-width:0;}
.pi-subject,.pi-label,.pi-value{min-width:0;overflow-wrap:anywhere;
word-break:break-word;white-space:normal;}
"""




def _single_html() -> str:
    items = "".join(
        '<div class="picker-item">'
        '<div class="pi-subject">%s</div>'
        '<div class="pi-label">%s</div>'
        '<div class="pi-value">%s</div>'
        '</div>' % (s, l, v)
        for s, l, v in LONG_ITEMS
    )
    modal = (
        '<div class="picker-overlay">'
        '<div class="picker-modal" style="max-width:min(%dpx, calc(100vw - 2rem));">'
        '<div class="picker-header"><h3>Start actie</h3>'
        '<button class="picker-close" data-action="close-warn">&times;</button>'
        '</div>'
        '<div class="picker-body">%s</div></div></div>' % (400, items)
    )
    return (
        "<!doctype html><html><head><meta charset=utf-8><style>%s</style></head>"
        "<body>%s</body></html>" % (CSS, modal)
    )
def _html() -> str:
    modals = []
    for px in (400, 560, 500, 480):
        items = "".join(
            '<div class="picker-item">'
            '<div class="pi-subject">%s</div>'
            '<div class="pi-label">%s</div>'
            '<div class="pi-value">%s</div>'
            "</div>" % (s, l, v)
            for s, l, v in LONG_ITEMS
        )
        modals.append(
            '<div class="picker-overlay">' '<div class="picker-modal" style="max-width:min(%dpx, calc(100vw - 2rem));">'
            '<div class="picker-header"><h3>Start actie</h3>'
            '<button class="picker-close" data-action="close-warn">&times;</button></div>'
            '<div class="picker-body">%s</div></div></div>' % (px, items)
        )
    return (
        "<!doctype html><html><head><meta charset=utf-8><style>%s</style></head>"
        "<body>%s</body></html>" % (CSS, "".join(modals))
    )


def _load(pw, viewport, single=False):
    page = pw.new_page(viewport=viewport)
    page.set_content(_single_html() if single else _html())
    return page


def _load_one(b, viewport):
    page = b.new_page(viewport=viewport)
    page.set_content(_single_html())
    page.evaluate(
        """() => {
          document.querySelectorAll('[data-action="close-warn"]').forEach(el => {
            el.onclick = () => el.closest('.picker-modal').remove();
          });
        }"""
    )
    page.evaluate(
        """() => {
          document.querySelectorAll('[data-action="close-warn"]').forEach(function(el){ el.onclick = function(){
            var overlay = el.closest('.picker-overlay');
            if (overlay) overlay.remove();
          }; });
        }"""
    )
    return page


def _metrics(page):
    return page.evaluate(
        """() => ({
          sw: document.documentElement.scrollWidth,
          cw: document.documentElement.clientWidth,
          modal: [...document.querySelectorAll('.picker-modal')]
            .map(m => Math.round(m.getBoundingClientRect().width)),
        })"""
    )


def test_mobile_390_no_horizontal_overflow():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load(b, {"width": 390, "height": 844})
        m = _metrics(page)
        assert m["sw"] <= m["cw"], (
            "390px: scrollWidth=%s > clientWidth=%s — modal of kind overschrijdt viewport"
            % (m["sw"], m["cw"])
        )
        for w in m["modal"]:
            assert w <= 390, "390px: modal %spx breder dan viewport" % w
            assert w > 200, "390px: modal %spx onverwacht smal — cap werkt niet" % w
        b.close()


def test_desktop_1280_keeps_560_cap():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load(b, {"width": 1280, "height": 800})
        m = _metrics(page)
        assert m["sw"] <= m["cw"], "1280px: scrollWidth=%s > clientWidth=%s" % (m["sw"], m["cw"])
        for w in m["modal"]:
            assert 390 <= w <= 560, "1280px: modal %spx buiten cap [390..560]" % w
        b.close()


def test_close_button_removes_modal_390():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = _load_one(b, {"width": 390, "height": 844})
        overlays_voor = page.evaluate(
            "() => document.querySelectorAll('.picker-overlay').length"
        )
        page.click('.picker-close[data-action="close-warn"]')
        overlays_na = page.evaluate(
            "() => document.querySelectorAll('.picker-overlay').length"
        )
        assert overlays_na == overlays_voor - 1, (
            "close verwijdert niet exact één overlay: %d -> %d"
            % (overlays_voor, overlays_na)
        )
        b.close()
