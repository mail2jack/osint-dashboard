#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Deploy Script
# ======================================
# Production deploy wrapper around scripts/update.sh:
#   preflight gate → (optional) pinned commit/tag → update.sh → readiness check
#
# There is NO automatic rollback. If the readiness check fails, follow
# RUNBOOK.md: rollback = git checkout <previous SHA> + scripts/restore.sh
# (alembic downgrade only by explicit manual instruction).
#
# Usage (as root, on the VPS):
#   /opt/osint-dashboard/scripts/deploy.sh                    # deploy master
#   /opt/osint-dashboard/scripts/deploy.sh <commit-sha|tag>   # deploy pinned ref
#   /opt/osint-dashboard/scripts/deploy.sh --dry-run          # plan, no changes
#
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
PIN=""
DRY=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        -h | --help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            if [ -n "$PIN" ]; then
                echo "ERROR: meerdere deploydoelen gegeven" >&2
                exit 2
            fi
            PIN="$arg"
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: draai als root (sudo su)" >&2
    exit 1
fi
if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: $APP_DIR bestaat niet — draai dit op de VPS" >&2
    exit 1
fi

if [ "$DRY" -eq 1 ]; then
    echo "=== DRY RUN — er wordt niets gewijzigd ==="
    echo "Deploydoel: ${PIN:-origin/master}"
    sudo bash "$APP_DIR/scripts/preflight.sh"
    echo ""
    echo "update.sh zou vervolgens draaien: backup, pull, units-sync, deps, build, migrate, restart, health."
    echo "=== DRY RUN VOLTOOID ==="
    exit 0
fi

echo "=== 1/4 Preflight ==="
if ! sudo bash "$APP_DIR/scripts/preflight.sh"; then
    echo "FAIL: preflight faalde — deploy gestopt."
    exit 1
fi

if [ -n "$PIN" ]; then
    echo "=== 2/4 Pinned deploydoel uitchecken: $PIN ==="
    sudo -u osint git -C "$APP_DIR" fetch --tags origin
    if ! sudo -u osint git -C "$APP_DIR" rev-parse --verify "$PIN" >/dev/null 2>&1; then
        echo "FAIL: $PIN is geen geldige commit of tag"
        exit 1
    fi
    sudo -u osint git -C "$APP_DIR" checkout "$PIN"
    echo "  uitgecheckt: $(sudo -u osint git -C "$APP_DIR" rev-parse HEAD)"
else
    echo "=== 2/4 origin/master deployen ==="
fi

echo "=== 3/4 update.sh (backup, pull, units-sync, deps, build, migrate, restart, health) ==="
sudo env DEPLOY_PIN="$PIN" bash "$APP_DIR/scripts/update.sh"

echo "=== 4/4 Readiness na restart ==="
READY=0
for i in 1 2 3 4 5; do
    if curl -fsS http://localhost:5000/health >/dev/null 2>&1; then
        READY=1
        break
    fi
    echo "  readiness nog niet groen (poging $i/5), wachten..."
    sleep 3
done

DEPLOYED_SHA=$(sudo -u osint git -C "$APP_DIR" rev-parse HEAD)
if [ "$READY" -eq 1 ]; then
    echo "OK: readiness groen"
    echo "$DEPLOYED_SHA" > "$APP_DIR/.deployed_sha"
    echo ""
    echo "Deploy voltooid — commit: $DEPLOYED_SHA"
else
    echo "FAIL: readiness niet groen na restart — NIET automatisch teruggedraaid." >&2
    echo "  Rollback-instructies: zie RUNBOOK.md (git checkout vorige SHA + restore.sh)." >&2
    exit 1
fi
