#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — CLI Update Script
# ===========================================
# Runs pre-deploy backup, git pull, pip install, alembic, and restart.
# Sends email notification to all superadmins upon completion.
#
# Safe to run only on the production server, as root. A deploy lock (flock)
# prevents two updates from running at the same time. Any failed step aborts
# the release — no half-finished deploys. Never auto-rolls back.
#
# Usage:
#   sudo ./scripts/update.sh
#   sudo DEPLOY_PIN=<commit-sha|tag> ./scripts/update.sh   # skip git pull
#
set -euo pipefail

DIR="/opt/osint-dashboard"
VENV_PYTHON="$DIR/venv/bin/python3"
ENV_FILE="$DIR/.env"
BACKUP_SCRIPT="$DIR/scripts/backup.sh"
NOTIFY_SCRIPT="$DIR/scripts/notify_update.py"
LOG_DIR="$DIR/logs"
LOCK_FILE="$DIR/.deploy.lock"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: draai als root (sudo ./scripts/update.sh)" >&2
    exit 1
fi
if [ ! -d "$DIR" ]; then
    echo "ERROR: $DIR bestaat niet — ben je op de productieserver?" >&2
    exit 1
fi
if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: geen venv gevonden op $VENV_PYTHON" >&2
    exit 1
fi

# --- 0/7 Deploy lock — no concurrent deploys ---
exec 9<>"$LOCK_FILE"
if ! flock -n 9; then
    echo "ERROR: een andere deploy draait al (lock: $LOCK_FILE)" >&2
    exit 1
fi

# --- Deploy log with timestamp + commit SHA (added after pull) ---
LOG_FILE="$LOG_DIR/update-$(date +%Y%m%d-%H%M%S).log"
if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR"
    chown osint:osint "$LOG_DIR"
fi
# Ignore SIGPIPE from the tee process-substitution below: writing to a pipe
# whose reader has gone away could otherwise kill the whole deploy with exit
# code 141 (SIGPIPE) mid-run. See issue with `exec > >(tee ...)` + `set -e`.
trap '' PIPE
exec > >(tee -a "$LOG_FILE") 2>&1
echo "==== update.sh start $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="

# Read DATABASE_URL from .env for sudo commands (sudo strips env vars)
DB_URL=""
if [ -f "$ENV_FILE" ]; then
    DB_URL=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)
fi

# Latest backup file, for the notification (best effort)
LATEST_BACKUP=""

notify() {
    # $1 = success|failed
    if [ -f "$NOTIFY_SCRIPT" ]; then
        (cd "$DIR" && sudo -u osint "$VENV_PYTHON" "$NOTIFY_SCRIPT" \
            --dir "$DIR" \
            --status "$1" \
            --backup "$LATEST_BACKUP") \
            || echo "WARNING: notification mislukt (alleen gelogd)"
    else
        echo "WARNING: notify_update.py niet gevonden — e-mail overgeslagen"
    fi
}

fail() {
    echo "FAIL: $1"
    notify "failed"
    echo "==== update.sh GEFAALD $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
    exit 1
}

# --- 1/7 Backup (must succeed — release stops otherwise) ---
echo "=== 1/7 Backup aanmaken ==="
if [ -f "$BACKUP_SCRIPT" ]; then
    if sudo -u osint bash "$BACKUP_SCRIPT" "$DIR/backups"; then
        echo "OK: backup gedaan"
        # `sort | head` under `set -o pipefail` exit 141 on SIGPIPE when head
        # stops early; this value is best-effort (notification only), so swallow.
        LATEST_BACKUP=$(find "$DIR/backups" -maxdepth 1 -name "iveras_backup_*.tar.gz.gpg" -type f 2>/dev/null | sort -r | head -1 || true)
    else
        fail "backup mislukt — release gestopt (geen half-afgemaakte deploy)"
    fi
else
    fail "backup.sh niet gevonden"
fi

# --- 2/7 Git pull (skipped when a pinned deploy already checked out the commit) ---
echo "=== 2/7 Code ophalen ==="
cd "$DIR"
if [ -n "${DEPLOY_PIN:-}" ]; then
    echo "DEPLOY_PIN gezet — git pull overgeslagen (gepinde commit al uitgecheckt)"
else
    sudo -u osint git pull origin "$(sudo -u osint git rev-parse --abbrev-ref HEAD 2>/dev/null || echo master)" \
        || fail "git pull mislukt"
fi
DEPLOYED_SHA=$(sudo -u osint git rev-parse HEAD)
echo "$DEPLOYED_SHA" > "$DIR/.deployed_sha"
echo "Gedeployed commit: $DEPLOYED_SHA"

# --- 3/8 Systemd-units synchroniseren (deploy -> /etc/systemd/system) ---
# Publiceert release-tracked units zodat live definities en repo in lockstep
# blijven. Enable/arm gebeurt bewust niet hier (zie scripts/sync_units.sh).
echo "=== 3/8 Systemd-units synchroniseren ==="
if [ -x "$DIR/scripts/sync_units.sh" ]; then
    sudo bash "$DIR/scripts/sync_units.sh" || fail "unit-sync mislukt"
else
    echo "WARNING: scripts/sync_units.sh niet gevonden — units niet gesynchroniseerd"
fi

# --- 4/8 Dependencies ---
echo "=== 4/8 Afhankelijkheden installeren ==="
sudo -u osint "$VENV_PYTHON" -m pip install -r "$DIR/requirements.txt" --quiet \
    || fail "pip install mislukt"

# --- 5/8 Frontend build ---
echo "=== 5/8 Frontend builden ==="
sudo -u osint env PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    node "$DIR/build.mjs" \
    || fail "frontend build mislukt"

# --- 6/8 Database migrations ---
echo "=== 6/8 Migraties draaien ==="
if [ -n "$DB_URL" ]; then
    sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic upgrade head \
        || fail "alembic upgrade mislukt"
else
    echo "WARNING: geen DATABASE_URL in .env — SQLite fallback"
    sudo -u osint "$VENV_PYTHON" -m alembic upgrade head \
        || fail "alembic upgrade mislukt"
fi

# --- 7/8 Restart ---
echo "=== 7/8 Service herstarten ==="
sudo systemctl restart osint-dashboard || fail "restart mislukt"

# --- 8/8 Health check na restart ---
echo "=== 8/8 Health check ==="
for i in 1 2 3 4 5; do
    if curl -fsS http://localhost:5000/api/v1/health >/dev/null 2>&1; then
        echo "OK: /api/v1/health bereikbaar"
        notify "success"
        echo "==== update.sh VOLTOOID $(date -u +%Y-%m-%dT%H:%M:%SZ) ($DEPLOYED_SHA) ===="
        echo "Log: $LOG_FILE"
        exit 0
    fi
    echo "  health nog niet bereikbaar (poging $i/5), wachten..."
    sleep 3
done
fail "health check na restart mislukt — NIET automatisch teruggedraaid, zie RUNBOOK.md"
