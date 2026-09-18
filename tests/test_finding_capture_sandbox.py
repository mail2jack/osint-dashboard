from scripts.verify_finding_capture_sandbox import probe_sandbox, verify_sandbox


def test_static_verifier_ignores_documentation_comments(tmp_path):
    unit = tmp_path / "worker.service"
    unit.write_text(
        "# --no-sandbox is forbidden\n"
        "User=osint\n"
        "NoNewPrivileges=true\n"
        "RestrictNamespaces=false\n"
    )
    chromium = tmp_path / "chromium"
    chromium.write_text("#!/bin/sh\n")
    chromium.chmod(0o755)

    ok, reasons = verify_sandbox(unit_path=unit, chromium_path=chromium)

    assert ok
    assert reasons == []


def test_static_verifier_rejects_unsafe_directive(tmp_path):
    unit = tmp_path / "worker.service"
    unit.write_text(
        "User=osint\n"
        "NoNewPrivileges=true\n"
        "RestrictNamespaces=false\n"
        "ExecStart=/usr/bin/chromium --no-sandbox\n"
    )
    chromium = tmp_path / "chromium"
    chromium.write_text("#!/bin/sh\n")
    chromium.chmod(0o755)

    ok, reasons = verify_sandbox(unit_path=unit, chromium_path=chromium)

    assert not ok
    assert reasons == ["worker unit permits an unsafe Chromium sandbox bypass"]


def test_sandbox_probe_uses_playwright_without_unsafe_flags(tmp_path, monkeypatch):
    chromium = tmp_path / "chromium"
    chromium.write_text("#!/bin/sh\n")
    chromium.chmod(0o755)
    class Page:
        url = "about:blank"

    class Context:
        def new_page(self):
            return Page()

        def close(self):
            pass

    class Browser:
        def new_context(self):
            return Context()

        def close(self):
            pass

    seen = {}

    class Chromium:
        def launch(self, **kwargs):
            seen.update(kwargs)
            return Browser()

    class Runtime:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: Runtime())
    ok, reason = probe_sandbox(chromium)
    assert ok
    assert reason == ""
    assert seen["executable_path"] == str(chromium)
    assert seen["timeout"] == 15_000
    assert "--no-sandbox" not in seen["args"]
    assert "--disable-setuid-sandbox" not in seen["args"]


def test_sandbox_probe_fails_closed(tmp_path, monkeypatch):
    chromium = tmp_path / "chromium"
    chromium.write_text("#!/bin/sh\n")
    chromium.chmod(0o755)
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: (_ for _ in ()).throw(OSError("blocked")),
    )
    ok, _reason = probe_sandbox(chromium)
    assert not ok


def test_sandbox_probe_uses_managed_playwright_lifecycle():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "scripts/verify_finding_capture_sandbox.py"
    ).read_text()
    assert "with sync_playwright()" in source
    assert "context.close()" in source
    assert "browser.close()" in source


def test_worker_unit_reads_only_the_managed_capture_environment_file():
    from pathlib import Path

    unit = (
        Path(__file__).resolve().parent.parent
        / "deploy/osint-finding-capture-worker.service"
    ).read_text()
    assert "Environment=FINDING_CAPTURE_WORKER_ENABLED=0" in unit
    assert "EnvironmentFile=-/etc/default/osint-finding-capture" in unit


def test_profile_template_is_narrow_and_never_targets_the_user_cache():
    from pathlib import Path

    profile = (
        Path(__file__).resolve().parent.parent
        / "deploy/osint-finding-capture-chromium.apparmor.in"
    ).read_text()
    assert "@CHROMIUM_PATH@" in profile
    assert "userns," in profile
    assert "flags=(unconfined)" in profile
    assert "/home/osint/.cache" not in profile
    assert "--no-sandbox" not in profile


def test_apparmor_installer_is_operator_only_and_never_enables_the_worker():
    from pathlib import Path

    installer = (
        Path(__file__).resolve().parent.parent
        / "scripts/install_finding_capture_apparmor_profile.sh"
    ).read_text()
    assert 'APP_DIR="${APP_DIR:-/opt/osint-dashboard}"' in installer
    assert 'CAPTURE_ROOT="${CAPTURE_ROOT:-/opt/osint-capture}"' in installer
    assert "guard_fixed_paths || exit 1" in installer
    assert "-m playwright install chromium" in installer
    assert "/home/osint/.cache" not in installer
    assert "-perm /022" in installer
    assert "apparmor_parser -r" in installer
    assert "systemctl daemon-reload" in installer
    assert "systemctl start" not in installer
    assert "systemctl restart" not in installer
    assert "FINDING_CAPTURE_WORKER_ENABLED=1" not in installer


def test_apparmor_runbook_preserves_the_global_sandbox_boundary():
    from pathlib import Path

    runbook = (
        Path(__file__).resolve().parent.parent
        / "docs/runbook-finding-capture-apparmor.md"
    ).read_text()
    assert "--no-sandbox" in runbook
    assert "do not disable AppArmor globally" in runbook
    assert "worker remains disabled" in runbook
