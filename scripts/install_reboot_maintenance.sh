#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Reboot-Maintenance Installer
# ======================================================
# Installs the one-shot systemd units for the planned kernel-reboot window:
#   - osint-reboot-maintenance.{service,timer}   (runs scripts/reboot_maintenance.sh
#                                                daily at 02:00 UTC)
#   - osint-reboot-verify.service                (runs scripts/reboot_verify.sh after
#                                                boot; no-op unless the marker exists)
# The service units are already published by deploy/sync_units.sh; this script
# only installs what sync_units deliberately does not: enable/arm.
#
# Usage:
#   sudo ./scripts/install_reboot_maintenance.sh            # install + arm timer
#   sudo ./scripts/install_reboot_maintenance.sh --disable  # disarm (timer + verify)
#
set -euo pipefail

APP_DIR=/opt/osint-dashboard
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: $APP_DIR bestaat niet — ben je op de productieserver?" >&2
    exit 1
fi

if [ "${1:-}" = "--disable" ]; then
    echo "=== Reboot-maintenance uitzetten ==="
    systemctl disable --now osint-reboot-maintenance.timer 2>/dev/null || true
    rm -f "$APP_DIR/.reboot_maintenance_armed"
    echo "  timer disabled; marker verwijderd. Reboot-verify blijft geïnstalleerd (no-op)."
    exit 0
fi

echo "=== Reboot-maintenance installeren ==="
systemctl daemon-reload
systemctl enable --now osint-reboot-maintenance.timer
systemctl enable osint-reboot-verify.service
systemctl start osint-reboot-verify.service   # no-op (geen marker)
echo ""
echo "  Timer:  $(systemctl list-timers osint-reboot-maintenance.timer --no-legend --no-pager | awk '{print $5, $6, $7}')"
echo "  Mode:   02:00 UTC, eenmalige reboot (gates -> backup if stale -> apt upgrade -> reboot)."
echo "  Verify: osint-reboot-verify.service draait na boot; log: /var/log/osint-reboot-verify.log."
echo "  Abort:  sudo /opt/osint-dashboard/scripts/reboot_maintenance.sh --abort"