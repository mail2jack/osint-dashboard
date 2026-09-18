#!/usr/bin/env bash
# Explicitly arm the reviewed finding-capture queue timer.
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="/etc/default/osint-finding-capture"
TIMER="osint-finding-capture-worker.timer"

if [ "${1:-}" != "--enable" ]; then
    echo "Usage: sudo $0 --enable" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi
test -f "$APP_DIR/deploy/osint-finding-capture-worker.service"
test -f "$APP_DIR/deploy/osint-finding-capture-worker.timer"
test -f "$ENV_FILE"
grep -q '^FINDING_CAPTURE_CHROMIUM_PATH=/' "$ENV_FILE"

tmp="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
grep -v '^FINDING_CAPTURE_WORKER_ENABLED=' "$ENV_FILE" > "$tmp" || true
printf 'FINDING_CAPTURE_WORKER_ENABLED=1\n' >> "$tmp"
install -o root -g root -m 0600 "$tmp" "$ENV_FILE"

install -o root -g root -m 0644 \
    "$APP_DIR/deploy/osint-finding-capture-worker.service" \
    "$APP_DIR/deploy/osint-finding-capture-worker.timer" \
    "$UNIT_DIR/"
systemctl daemon-reload
systemd-analyze verify "$UNIT_DIR/osint-finding-capture-worker.service" "$UNIT_DIR/osint-finding-capture-worker.timer"
systemctl enable --now "$TIMER"
systemctl is-enabled --quiet "$TIMER"
systemctl is-active --quiet "$TIMER"
echo "Finding capture timer enabled (15-second poll; Chromium starts only for queued work)."
