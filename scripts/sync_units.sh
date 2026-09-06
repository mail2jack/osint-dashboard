#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Systemd Unit Sync
# ==========================================
# Copies the release-tracked systemd units (deploy/*.service and deploy/*.timer)
# into /etc/systemd/system and reloads systemd, so every deploy keeps the live
# unit definitions in lockstep with the repository.
#
# Scope:
#   - Only units under $APP_DIR/deploy are published.
#   - osint-dashboard.service and its override drop-in are intentionally NOT
#     touched (they live outside deploy/ and are managed by their own install).
#   - Units are NEVER enabled/started here. Enabling/arming remains the job of
#     the dedicated install scripts (e.g. install_canary_close.sh), so a deploy
#     does not accidentally start a one-shot canary timer or an alert unit.
#
# Usage:
#   sudo ./scripts/sync_units.sh
#
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
SRC_DIR="$APP_DIR/deploy"
UNIT_DIR="/etc/systemd/system"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi
if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: $SRC_DIR bestaat niet — ben je op de productieserver?" >&2
    exit 1
fi

echo "=== Systemd-units synchroniseren (deploy -> /etc/systemd/system) ==="
CHANGED=0
COPIED=0
for unit in "$SRC_DIR"/*.service "$SRC_DIR"/*.timer; do
    [ -f "$unit" ] || continue
    name="$(basename "$unit")"
    COPIED=$((COPIED + 1))
    if ! cmp -s "$unit" "$UNIT_DIR/$name"; then
        echo "  -> $name (gewijzigd/nieuw)"
        install -o root -g root -m 0644 "$unit" "$UNIT_DIR/$name"
        CHANGED=1
    fi
done

systemctl daemon-reload

if [ "$CHANGED" -eq 1 ]; then
    echo "OK: $COPIED units bekeken, wijzigingen doorgevoerd en systemd herladen."
else
    echo "OK: $COPIED units volledig overeenkomend — geen wijzigingen, systemd herladen."
fi