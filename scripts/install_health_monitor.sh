#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/osint-dashboard
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

chmod 0755 "$APP_DIR/scripts/monitor_health_light.sh"
install -o root -g root -m 0644 "$APP_DIR/deploy/osint-health-monitor.service" \
    /etc/systemd/system/osint-health-monitor.service
systemctl daemon-reload
systemctl enable --now osint-health-monitor.service
