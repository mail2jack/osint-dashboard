#!/usr/bin/env bash
# Explicitly arm the reviewed passive workflow source-research worker.
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="/etc/default/osint-source-research-worker"
SERVICE="osint-source-research-worker.service"

if [ "${1:-}" != "--enable" ]; then
    echo "Usage: sudo $0 --enable" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi
test -f "$APP_DIR/deploy/$SERVICE"
test -x "$APP_DIR/scripts/run_source_research_worker.py"

tmp="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf 'WORKFLOW_SOURCE_RESEARCH_WORKER_ENABLED=1\n' > "$tmp"
printf 'WORKFLOW_SOURCE_RESEARCH_POLL_SECONDS=15\n' >> "$tmp"
install -o root -g root -m 0644 "$tmp" "$ENV_FILE"
install -o root -g root -m 0644 "$APP_DIR/deploy/$SERVICE" "$UNIT_DIR/"
systemctl daemon-reload
systemd-analyze verify "$UNIT_DIR/$SERVICE"
systemctl enable --now "$SERVICE"
systemctl is-enabled --quiet "$SERVICE"
systemctl is-active --quiet "$SERVICE"
echo "Passive workflow source-research worker enabled (15-second poll)."
