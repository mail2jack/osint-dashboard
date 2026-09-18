import subprocess

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


def test_sandbox_probe_never_uses_unsafe_flags(tmp_path, monkeypatch):
    chromium = tmp_path / "chromium"
    chromium.write_text("#!/bin/sh\n")
    chromium.chmod(0o755)
    command = []

    def fake_run(args, **_kwargs):
        command.extend(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("scripts.verify_finding_capture_sandbox.subprocess.run", fake_run)
    ok, reason = probe_sandbox(chromium)
    assert ok
    assert reason == ""
    assert "--no-sandbox" not in command
    assert "--disable-setuid-sandbox" not in command
    assert command[-1] == "about:blank"


def test_sandbox_probe_fails_closed(tmp_path, monkeypatch):
    chromium = tmp_path / "chromium"
    chromium.write_text("#!/bin/sh\n")
    chromium.chmod(0o755)
    monkeypatch.setattr(
        "scripts.verify_finding_capture_sandbox.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1),
    )
    ok, _reason = probe_sandbox(chromium)
    assert not ok
