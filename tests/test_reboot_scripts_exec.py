"""Regression: reboot scripts must stay executable.

These scripts are executed by systemd (osint-reboot-maintenance.timer and
osint-reboot-verify.service). If the executable bit drops out of git (100644),
every deployment locally re-chmods them. Keep the bit tracked in-repo instead.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REBOOT_SCRIPTS = (
    "scripts/reboot_maintenance.sh",
    "scripts/reboot_verify.sh",
)


def test_reboot_scripts_are_executable():
    for relative in REBOOT_SCRIPTS:
        script = ROOT / relative
        assert script.is_file(), f"{relative} ontbreekt"
        assert os.access(script, os.X_OK), (
            f"{relative} is niet executable (verwacht mode 100755 in git)"
        )