#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Systemd Hardening Installer
# =====================================================
# Installs deploy/osint-dashboard-hardening.override.conf as the systemd
# drop-in /etc/systemd/system/osint-dashboard.service.d/hardening.conf,
# verifies the unit, restarts the service and runs a health check.
#
# Fail-closed: if systemd verification or the health check fails, the drop-in
# is removed again and the service restarts WITHOUT the hardening — the
# drop-in is configuration only, so reverting it is safe and does not touch
# code, state or the database (it is NOT a code rollback).
#
# Usage:
#   sudo ./scripts/install_dashboard_hardening.sh          # install + verify
#   sudo ./scripts/install_dashboard_hardening.sh --dry-run  # plan, no changes
#   sudo ./scripts/install_dashboard_hardening.sh --remove   # uninstall drop-in
#
set -euo pipefail

APP_DIR=/opt/osint-dashboard
SRC="$APP_DIR/deploy/osint-dashboard-hardening.override.conf"
DST_DIR="/etc/systemd/system/osint-dashboard.service.d"
DST="$DST_DIR/hardening.conf"
SERVICE=osint-dashboard
HEALTH_URL=http://localhost:5000/api/v1/health

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: draai als root (sudo ./scripts/install_dashboard_hardening.sh)" >&2
    exit 1
fi
if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: $APP_DIR bestaat niet — ben je op de productieserver?" >&2
    exit 1
fi

exposure() {
    # Print the overall systemd-analyze exposure level (config-only read).
    systemd-analyze security "$SERVICE" 2>/dev/null \
        | grep -oE "[0-9]+\.[0-9]+" | tail -1 || echo "n/a"
}

health_ok() {
    curl -fsS "$HEALTH_URL" >/dev/null 2>&1
}

wait_health() {
    for i in 1 2 3 4 5; do
        if health_ok; then
            return 0
        fi
        echo "  health nog niet bereikbaar (poging $i/5), wachten..."
        sleep 3
    done
    return 1
}

if [ "${1:-}" = "--dry-run" ]; then
    echo "=== DRY RUN — er wordt niets gewijzigd ==="
    echo "Bron:  $SRC"
    echo "Doel:  $DST"
    if [ -f "$SRC" ]; then
        echo "Inhoud claimt:"
        grep -E "^[A-Za-z].*=" "$SRC" | sed "s/^/  /"
    else
        echo "ERROR: $SRC bestaat niet" >&2
        exit 1
    fi
    echo "Huidige exposure: $(exposure)"
    echo "=== DRY RUN VOLTOOID ==="
    exit 0
fi

if [ "${1:-}" = "--remove" ]; then
    echo "=== Hardening-drop-in verwijderen ==="
    if [ -f "$DST" ]; then
        rm -f "$DST"
        systemctl daemon-reload
        systemctl restart "$SERVICE"
        echo "OK: $DST verwijderd, service herstart zonder hardening."
    else
        echo "OK: geen hardening-drop-in aanwezig ($DST)."
    fi
    exit 0
fi

echo "=== Systemd-hardening osint-dashboard ==="
BEFORE=$(exposure)
echo "Exposure vóór: $BEFORE"

if [ ! -f "$SRC" ]; then
    echo "ERROR: $SRC niet gevonden" >&2
    exit 1
fi
if [ -f "$DST" ] && cmp -s "$SRC" "$DST"; then
    echo "OK: drop-in is al identiek geïnstalleerd — alleen herverifiëren."
    INSTALLED=0
else
    install -d -o root -g root -m 0755 "$DST_DIR"
    install -o root -g root -m 0644 "$SRC" "$DST"
    systemctl daemon-reload
    INSTALLED=1
fi

echo "=== systemd-analyze verify ==="
if ! systemd-analyze verify /etc/systemd/system/osint-dashboard.service "$DST"; then
    echo "FAIL: unitverificatie mislukt — hardening terugdraaien."
    if [ "${INSTALLED:-0}" -eq 1 ]; then
        rm -f "$DST"
        systemctl daemon-reload
    fi
    systemctl restart "$SERVICE"
    exit 1
fi

echo "=== Service herstarten (met hardening) ==="
systemctl restart "$SERVICE"

echo "=== Health check ==="
if wait_health; then
    AFTER=$(exposure)
    echo "OK: health bereikbaar."
    echo "Exposure vóór: $BEFORE → na: $AFTER"
    echo ""
    echo "Hardening actief: $DST"
    echo "Actuele unit-settings: systemctl cat $SERVICE | grep -E 'Protect|NoNew|Private|Restrict|Capability|SystemCall|UMask|ReadWrite|LockPers'"
else
    echo "FAIL: health niet bereikbaar met hardening — drop-in terugdraaien."
    if [ "${INSTALLED:-0}" -eq 1 ]; then
        rm -f "$DST"
        systemctl daemon-reload
    fi
    systemctl restart "$SERVICE"
    if wait_health; then
        echo "OK: service hersteld zonder hardening (exposure: $(exposure))."
    else
        echo "CRITICAL: ook zonder hardening geen health — zie RUNBOOK.md." >&2
    fi
    exit 1
fi