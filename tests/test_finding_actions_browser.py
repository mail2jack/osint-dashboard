"""Browser-level regression tests for investigation workspace finding actions.

Proves in real Chromium:
  - 390px viewport: finding action controls don't overflow horizontally;
  - reject double-click guard: exactly one POST is sent even with rapid clicks;
  - comment auto-save: an out-of-order (stale) response triggers a trailing
    save so the newest text always lands last in the DB and the UI.

Requires Playwright (``pip install playwright && playwright install chromium``).
Falls back gracefully when Chromium is not installed.
"""

import re
import socket
import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
import requests as _requests
from werkzeug.serving import make_server

from cms.models import (
    ActionFinding,
    Client as CmsClient,
    FeatureFlag,
    Finding,
    Investigation,
    ResearchAction,
    Subject,
    User,
    db,
)
from cms.workflow.routes import WorkflowCase, WorkflowScreenshot

PW = pytest.importorskip("playwright.sync_api", reason="requires playwright")

WORKSPACE_FLAG = "investigation_workspace"
INVESTIGATION_URL = "/cms/workflow/case/{case_id}/investigations/{inv_id}"


def _enable_workspace(tenant_id):
    flag = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name=WORKSPACE_FLAG
    ).first()
    if flag:
        flag.enabled = True
    else:
        flag = FeatureFlag(
            tenant_id=tenant_id, flag_name=WORKSPACE_FLAG, enabled=True
        )
        db.session.add(flag)
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
    base = f"http://127.0.0.1:{port}"
    session.get(base + "/auth/login")
    resp = session.post(
        base + "/auth/login",
        data={"email": "admin@localhost", "password": "Test1234!"},
        allow_redirects=False,
    )
    location = resp.headers.get("Location", "")
    if location.endswith("/auth/2fa/setup"):
        session.get(base + location)
        import pyotp
        match = re.search(r"<code>([A-Z2-7]+)</code>", session.get(base + location).text)
        assert match, "2FA setup secret missing"
        resp = session.post(
            base + location,
            data={"code": pyotp.TOTP(match.group(1)).now()},
            allow_redirects=True,
        )
        return resp.url, base
    if location.endswith("/auth/2fa/verify"):
        session.get(base + location)
        import pyotp
        resp = session.post(
            base + location,
            data={"code": pyotp.TOTP("JBSWY3DPEHPK3PXP").now()},
            allow_redirects=True,
        )
        return resp.url, base
    if location:
        return session.get(base + location).url, base
    return resp.url, base


@pytest.fixture
def workspace_case(app):
    """Seed a case + investigation + finding and enable the workspace flag."""
    with app.app_context():
        admin = User.query.filter_by(role="admin").first()
        tenant_id = admin.tenant_id
        orig_totp = admin.totp_secret
        orig_enabled = admin.totp_enabled
        orig_super = admin.is_super_admin
        admin.totp_secret = "JBSWY3DPEHPK3PXP"
        admin.totp_enabled = True
        admin.is_super_admin = False
        db.session.commit()
        _enable_workspace(tenant_id)

        client = CmsClient(name="Browser Client", is_active=True, tenant_id=tenant_id)
        db.session.add(client)
        db.session.flush()
        case = WorkflowCase(
            tenant_id=tenant_id,
            case_number=f"BR-{uuid.uuid4().hex[:6].upper()}",
            client_id=client.id,
            title="Browser Guard Case",
            priority="medium",
            status="open",
            start_date=datetime.now(UTC).date(),
        )
        db.session.add(case)
        db.session.flush()
        case.created_by = admin.id

        inv = Investigation(
            tenant_id=tenant_id,
            case_id=case.id,
            sequence_no=1,
            title="Browser Investigation",
            status="open",
        )
        db.session.add(inv)
        db.session.flush()

        subj = Subject(
            tenant_id=tenant_id,
            name="Browser Subject",
            subject_type="person",
            email="br@example.com",
        )
        subj.encrypt_identifiers()
        db.session.add(subj)
        db.session.flush()

        action = ResearchAction(
            case_id=case.id,
            tenant_id=tenant_id,
            subject_id=subj.id,
            investigation_id=inv.id,
            target_kind="subject",
            action_type="subdomain",
            label="Browser Action",
            status="completed",
            completed_at=datetime.now(UTC),
        )
        db.session.add(action)
        db.session.flush()

        finding = Finding(
            tenant_id=tenant_id,
            case_id=case.id,
            subject_id=subj.id,
            title="Browser Finding",
            content="Browser evidence",
            source_type="manual",
            status="candidate",
            verified=False,
            created_by=admin.id,
        )
        db.session.add(finding)
        db.session.flush()
        db.session.add(ActionFinding(action_id=action.id, finding_id=finding.id))
        db.session.commit()
        case_id = str(case.id)
        inv_id = str(inv.id)
        action_id = str(action.id)
        finding_id = str(finding.id)
        subject_id = str(subj.id)
        client_id = str(client.id)
        yield case_id, inv_id

        # Teardown: clean up seed data
        db.session.rollback()
        ActionFinding.query.filter_by(action_id=action_id).delete(
            synchronize_session=False
        )
        Finding.query.filter_by(case_id=case_id).delete(synchronize_session=False)
        ResearchAction.query.filter_by(case_id=case_id).delete(
            synchronize_session=False
        )
        Investigation.query.filter_by(case_id=case_id).delete(
            synchronize_session=False
        )
        WorkflowCase.query.filter_by(id=case_id).delete(synchronize_session=False)
        WorkflowScreenshot.query.filter_by(finding_id=finding_id).delete(
            synchronize_session=False
        )
        from cms.models import case_subjects
        db.session.execute(case_subjects.delete().where(case_subjects.c.case_id == case_id))
        Subject.query.filter_by(id=subject_id).delete(synchronize_session=False)
        CmsClient.query.filter_by(id=client_id).delete(synchronize_session=False)
        admin.totp_secret = orig_totp
        admin.totp_enabled = orig_enabled
        admin.is_super_admin = orig_super
        db.session.commit()


def _browser_diagnostics(page):
    diagnostics = {"console": [], "page": [], "requests": []}
    page.on(
        "console",
        lambda m: diagnostics["console"].append(m.text) if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: diagnostics["page"].append(str(e)))
    page.on(
        "requestfailed",
        lambda r: diagnostics["requests"].append(f"{r.url}: {r.failure}"),
    )
    return diagnostics


def test_finding_actions_390px_no_overflow(app, workspace_case):
    server, thread, port = _start_server(app)
    try:
        s = _requests.Session()
        _, base = _login_http(s, port)
        case_id, inv_id = workspace_case
        with PW.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 390, "height": 844})
                try:
                    ctx.add_cookies([
                        {"name": c.name, "value": c.value,
                         "domain": "127.0.0.1", "path": "/"}
                        for c in s.cookies
                    ])
                    page = ctx.new_page()
                    diag = _browser_diagnostics(page)
                    resp = page.goto(f"{base}{INVESTIGATION_URL.format(case_id=case_id, inv_id=inv_id)}")
                    assert resp is not None and resp.status == 200
                    page.wait_for_selector(".finding-item", timeout=5000)
                    sw = page.evaluate("() => document.documentElement.scrollWidth")
                    cw = page.evaluate("() => document.documentElement.clientWidth")
                    assert sw <= cw, (
                        f"390px workspace: scrollWidth {sw} > clientWidth {cw}"
                    )
                    first_item = page.locator(".finding-item").first
                    item_box = first_item.bounding_box()
                    assert item_box is not None, "finding-item has no bounding box"
                    assert item_box["x"] >= 0 and item_box["x"] + item_box["width"] <= 390
                    assert diag == {"console": [], "page": [], "requests": []}, diag
                finally:
                    ctx.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_reject_double_click_sends_one_request(app, workspace_case):
    server, thread, port = _start_server(app)
    try:
        s = _requests.Session()
        _, base = _login_http(s, port)
        case_id, inv_id = workspace_case
        with PW.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                try:
                    ctx.add_cookies([
                        {"name": c.name, "value": c.value,
                         "domain": "127.0.0.1", "path": "/"}
                        for c in s.cookies
                    ])
                    page = ctx.new_page()
                    page.on("dialog", lambda d: d.accept())

                    verify_requests = []

                    def _intercept(route):
                        verify_requests.append(route.request)
                        page.wait_for_timeout(700)
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body='{"ok": true}',
                        )

                    page.route("**/api/case/*/findings/*/verify", _intercept)

                    resp = page.goto(f"{base}{INVESTIGATION_URL.format(case_id=case_id, inv_id=inv_id)}")
                    assert resp is not None and resp.status == 200
                    page.wait_for_selector('[data-action="reject-finding"]', timeout=5000)
                    reject_btn = page.locator('[data-action="reject-finding"]').first
                    reject_btn.click()
                    # Second activation while the first request is still in-flight
                    # must be swallowed by the data-busy guard.
                    reject_btn.dispatch_event("click")
                    page.wait_for_timeout(1100)
                    assert len(verify_requests) == 1, (
                        f"expected 1 verify POST, got {len(verify_requests)}"
                    )
                finally:
                    ctx.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_comment_autosave_out_of_order_keeps_newest(app, workspace_case):
    # Delay the FIRST comment response server-side (2s) so the second POST's
    # response reaches the browser first — i.e. the stale response arrives last.
    # Production client JS is untouched; no Playwright route interception.
    comment_view = app.view_functions["workflow.save_comment"]
    call_state = {"count": 0}

    def _delayed_comment(**kwargs):
        call_state["count"] += 1
        resp = comment_view(**kwargs)
        if call_state["count"] == 1:
            time.sleep(2.0)
        return resp

    app.view_functions["workflow.save_comment"] = _delayed_comment

    server, thread, port = _start_server(app)
    try:
        s = _requests.Session()
        _, base = _login_http(s, port)
        case_id, inv_id = workspace_case
        with app.app_context():
            finding = Finding.query.filter_by(
                case_id=case_id, title="Browser Finding"
            ).first()
            finding_id = str(finding.id)
        with PW.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                try:
                    ctx.add_cookies([
                        {"name": c.name, "value": c.value,
                         "domain": "127.0.0.1", "path": "/"}
                        for c in s.cookies
                    ])
                    page = ctx.new_page()
                    diag = _browser_diagnostics(page)
                    comment_requests = []
                    page.on(
                        "request",
                        lambda r: comment_requests.append(r)
                        if "/findings/" in r.url and r.url.endswith("/comment")
                        else None,
                    )

                    resp = page.goto(
                        f"{base}{INVESTIGATION_URL.format(case_id=case_id, inv_id=inv_id)}"
                    )
                    assert resp is not None and resp.status == 200
                    page.wait_for_selector(".finding-item", timeout=5000)
                    ta = page.locator('[data-action="comment-input"]').first

                    # First save (older text): fired ~600ms after input; the
                    # view holds its response for 2s.
                    ta.fill("out-of-order A")
                    page.wait_for_timeout(800)
                    # Keep typing while the first request is in flight: the
                    # debounced second save (newest text) is dispatched and its
                    # response returns first.
                    ta.fill("out-of-order AB")
                    page.wait_for_timeout(800)
                    assert len(comment_requests) == 2, (
                        "expected the two comment saves, "
                        f"got {len(comment_requests)}"
                    )

                    # Once the stale (first) response finally arrives, the seq
                    # guard must schedule a trailing save carrying newest text.
                    deadline = time.time() + 8
                    while len(comment_requests) < 3 and time.time() < deadline:
                        page.wait_for_timeout(100)
                    assert len(comment_requests) == 3, (
                        "expected a trailing save after the stale response, "
                        f"got {len(comment_requests)} comment requests"
                    )
                    trailing = comment_requests[2]
                    assert '"out-of-order AB"' in trailing.post_data, (
                        "trailing save must carry the newest comment text"
                    )

                    page.wait_for_timeout(700)
                    assert diag == {"console": [], "page": [], "requests": []}, diag

                    with app.app_context():
                        comment = db.session.get(Finding, finding_id).comment
                    assert comment == "out-of-order AB", (
                        f"stale response overwrote newest comment, DB has {comment!r}"
                    )

                    page.reload()
                    page.wait_for_selector(".finding-item", timeout=5000)
                    reloaded_value = page.locator(
                        '[data-action="comment-input"]'
                    ).first.input_value()
                    assert reloaded_value == "out-of-order AB", (
                        f"UI shows {reloaded_value!r} after reload, "
                        "expected out-of-order AB"
                    )
                finally:
                    ctx.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        app.view_functions["workflow.save_comment"] = comment_view
