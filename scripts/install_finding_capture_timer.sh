#!/usr/bin/env bash
# Explicitly arm the reviewed, efficient finding-capture queue worker.
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="/etc/default/osint-finding-capture"
SERVICE="osint-finding-capture-worker.service"
LEGACY_TIMER="osint-finding-capture-worker.timer"

if [ "${1:-}" != "--enable" ]; then
    echo "Usage: sudo $0 --enable" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi
test -f "$APP_DIR/deploy/osint-finding-capture-worker.service"
test -f "$ENV_FILE"
grep -q '^FINDING_CAPTURE_CHROMIUM_PATH=/' "$ENV_FILE"

tmp="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
grep -v '^FINDING_CAPTURE_WORKER_ENABLED=' "$ENV_FILE" > "$tmp" || true
printf 'FINDING_CAPTURE_WORKER_ENABLED=1\n' >> "$tmp"
install -o root -g root -m 0600 "$tmp" "$ENV_FILE"

install -o root -g root -m 0644 \
    "$APP_DIR/deploy/osint-finding-capture-worker.service" "$UNIT_DIR/"
systemctl disable --now "$LEGACY_TIMER" 2>/dev/null || true
rm -f "$UNIT_DIR/$LEGACY_TIMER"
systemctl daemon-reload
systemd-analyze verify "$UNIT_DIR/$SERVICE"
systemctl enable --now "$SERVICE"
systemctl is-enabled --quiet "$SERVICE"
systemctl is-active --quiet "$SERVICE"
echo "Finding capture worker enabled (15-second poll; Chromium starts only for queued work)."
