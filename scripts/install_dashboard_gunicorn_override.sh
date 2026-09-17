#!/usr/bin/env bash
# Install the explicitly managed Gunicorn override without restarting the app.
# The operator must review the result and restart osint-dashboard separately.
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
SRC="$APP_DIR/deploy/osint-dashboard-gunicorn2.override.conf"
SERVICE="osint-dashboard.service"
DST_DIR="/etc/systemd/system/$SERVICE.d"
DST="$DST_DIR/override.conf"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$DST.backup-$STAMP"
TMP=""

cleanup() {
    if [ -n "$TMP" ] && [ -e "$TMP" ]; then
        rm -f "$TMP"
    fi
}
trap cleanup EXIT

restore_previous() {
    if [ -f "$BACKUP" ]; then
        install -o root -g root -m 0644 "$BACKUP" "$DST"
    else
        rm -f "$DST"
    fi
    systemctl daemon-reload
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo ./scripts/install_dashboard_gunicorn_override.sh)" >&2
    exit 1
fi
if [ ! -f "$SRC" ]; then
    echo "ERROR: source override not found: $SRC" >&2
    exit 1
fi
if [ ! -d "$DST_DIR" ]; then
    install -d -o root -g root -m 0755 "$DST_DIR"
fi
if [ -e "$DST" ]; then
    if [ -e "$BACKUP" ]; then
        echo "ERROR: backup already exists: $BACKUP" >&2
        exit 1
    fi
    cp -p "$DST" "$BACKUP"
    echo "Backup: $BACKUP"
fi

# Write in the target directory, then rename atomically over the drop-in.
TMP="$(mktemp "$DST_DIR/.override.conf.tmp.XXXXXX")"
install -o root -g root -m 0644 "$SRC" "$TMP"
mv -f "$TMP" "$DST"
TMP=""

if ! systemctl daemon-reload; then
    echo "ERROR: systemd daemon-reload failed; restoring prior override" >&2
    restore_previous
    exit 1
fi
if command -v systemd-analyze >/dev/null 2>&1; then
    if ! systemd-analyze verify "$SERVICE"; then
        echo "ERROR: systemd unit verification failed; restoring prior override" >&2
        restore_previous
        exit 1
    fi
else
    echo "WARNING: systemd-analyze unavailable; unit not verified" >&2
fi

echo "OK: installed $DST"
echo "IMPORTANT: no service restart was performed."
echo "Review the unit, then explicitly run: systemctl restart $SERVICE"
