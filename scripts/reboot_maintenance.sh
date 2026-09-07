#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Planned Kernel-Reboot Maintenance
# ==========================================================
# Runs at a scheduled window (osint-reboot-maintenance.timer):
#   gates -> backup (if stale) -> apt upgrade -> reboot into newest kernel.
#
# Fail-closed: any failed gate/step aborts WITHOUT rebooting; the operator
# reviews /var/log/osint-reboot-maintenance.log afterwards and re-arms.
# Lives in the repo (scripts/) so it follows deploys; the timer unit is in
# deploy/ and is enabled by scripts/install_reboot_maintenance.sh.
#
# Usage:
#   sudo ./scripts/reboot_maintenance.sh            # run the whole window
#   sudo ./scripts/reboot_maintenance.sh --check    # gates only, no changes
#   sudo ./scripts/reboot_maintenance.sh --abort    # disarm + remove marker
#
set -euo pipefail

DIR="/opt/osint-dashboard"
LOG="/var/log/osint-reboot-maintenance.log"
MARKER="$DIR/.reboot_maintenance_armed"
BACKUP_SCRIPT="$DIR/scripts/backup.sh"
OUTDATED_HOURS=12

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: draai als root (sudo ./scripts/reboot_maintenance.sh)" >&2
    exit 1
fi
if [ ! -d "$DIR" ]; then
    echo "ERROR: $DIR bestaat niet — ben je op de productieserver?" >&2
    exit 1
fi

exec > >(tee --append "$LOG" >(logger -t osint-reboot-maintenance -p user.info) >/dev/null) 2>&1
echo "==== reboot_maintenance start $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="

if [ "${1:-}" = "--abort" ]; then
    rm -f "$MARKER"
    echo "OK: marker verwijderd — er wordt niet gerboot."
    exit 0
fi

fail() {
    echo "FAIL: $1 — geen reboot."
    echo "==== reboot_maintenance GEFAALD $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
    exit 1
}

echo "=== 1/4 Gates ==="
if [ -n "${DEPLOY_BYPASS:-}" ]; then
    echo "  DEPLOY_BYPASS gezet — gates overgeslagen (alleen handmatig)."
else
    # Deploy-lock: geen update.sh tegelijkertijd in dezelfde nacht.
    if ! flock -n "$DIR/.deploy.lock" true 2>/dev/null; then
        fail "deploy-lock bezet — wacht tot update.sh klaar is en re-arm."
    fi
    # Units die na boot moeten draaien; NIET forceer-enabled: alleen waarschuwen.
    for u in osint-dashboard postgresql nginx spiderfoot; do
        if ! systemctl is-enabled "$u" >/dev/null 2>&1; then
            echo "  WARN: $u niet enabled — wordt niet gestart na reboot."
        else
            echo "  OK: $u enabled."
        fi
    done
    # Geen other failed queued: reboott niet als er actieve problemen zijn.
    if systemctl is-active --quiet osint-dashboard; then
        echo "  OK: osint-dashboard actief."
    else
        fail "osint-dashboard niet actief vóór reboot — eerst incident oplossen."
    fi
fi

if [ "${1:-}" = "--check" ]; then
    echo "CHECK: gates doorstaan — script stopt hier, niets gewijzigd."
    echo "==== reboot_maintenance CHECK VOLTOOID $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
    exit 0
fi

echo "=== 2/4 Backup (indien ouder dan ${OUTDATED_HOURS}u) ==="
LATEST=""
if [ -d "$DIR/backups" ]; then
    LATEST=$(find "$DIR/backups" -maxdepth 1 -name "iveras_backup_*.tar.gz.gpg" -newermt "-${OUTDATED_HOURS} hours" 2>/dev/null | head -1 || true)
fi
if [ -z "$LATEST" ]; then
    if [ -x "$BACKUP_SCRIPT" ]; then
        echo "  geen verse backup gevonden — backup maken."
        sudo -u osint bash "$BACKUP_SCRIPT" "$DIR/backups" || fail "backup mislukt"
    else
        fail "backup.sh ontbreekt en er is geen verse backup — geen reboot zonder."
    fi
else
    echo "  OK: verse backup aanwezig ($(basename "$LATEST"))."
fi

echo "=== 3/4 apt upgrade ==="
apt-get update -qq || fail "apt-get update mislukt"
DEBIAN_FRONTEND=noninteractive apt-get -y \
    -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
    upgrade || fail "apt-get upgrade mislukt"

echo "=== 4/4 Reboot plannen ==="
KERNEL_NOW=$(uname -r)
KERNEL_LATEST=$(ls -1 /boot/vmlinuz-* 2>/dev/null | sed 's|.*vmlinuz-||' | sort -V | tail -1 || echo "")
if [ -n "$KERNEL_LATEST" ] && [ "$KERNEL_NOW" != "$KERNEL_LATEST" ]; then
    echo "  draaiende kernel: $KERNEL_NOW → nieuwste: $KERNEL_LATEST"
else
    echo "  kernel al nieuwste ($KERNEL_NOW); reboot activeert geen nieuwe kernel."
fi

touch "$MARKER"
echo "  marker gezet ($MARKER)."
echo "==== reboot_maintenance rebootet $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
systemctl reboot
# systemctl reboot keert niet terug; fallback-echo voor het geval:
echo "WARN: systemctl reboot keerde terug — handmatig rebooten." >&2
exit 1