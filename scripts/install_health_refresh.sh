#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
UNIT_DIR="/etc/systemd/system"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

install -o root -g root -m 0644 \
    "$APP_DIR/deploy/osint-health-refresh.service" \
    "$APP_DIR/deploy/osint-health-refresh.timer" \
    "$UNIT_DIR/"
systemctl daemon-reload
systemctl enable --now osint-health-refresh.timer
systemctl start osint-health-refresh.service
systemctl is-enabled --quiet osint-health-refresh.timer
echo "Health refresh timer installed and enabled."
