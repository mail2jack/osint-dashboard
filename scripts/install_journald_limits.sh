#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/osint-dashboard
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

install -o root -g root -m 0644 "$APP_DIR/deploy/60-osint-journald-limits.conf" \
    /etc/systemd/journald.conf.d/60-osint-journald-limits.conf
systemctl restart systemd-journald.service
journalctl --vacuum-time=14d --vacuum-size=1G
