#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — CLI Update Script
# ===========================================
# Runs backup, pip install, git pull, alembic, and restart.
# Sends email notification to all superadmins upon completion.
#
# Usage:
#   ./scripts/update.sh
#

set -uo pipefail

DIR="/opt/osint-dashboard"
VENV_PYTHON="$DIR/venv/bin/python3"
ENV_FILE="$DIR/.env"
OVERALL_STATUS="success"
LATEST_BACKUP=""

if [ ! -d "$DIR" ]; then
    echo "❌ $DIR bestaat niet — ben je op de productie-server?"
    exit 1
fi
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Geen venv gevonden op $VENV_PYTHON"
    exit 1
fi

# Lees DATABASE_URL uit .env voor sudo-commands (sudo stripped env vars)
DB_URL=""
if [ -f "$ENV_FILE" ]; then
    DB_URL=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)
fi

BACKUP_SCRIPT="$DIR/scripts/backup.sh"
NOTIFY_SCRIPT="$DIR/scripts/notify_update.py"

# --- 1/5 Backup ---
echo "=== 1/5 Backup maken ==="
if [ -f "$BACKUP_SCRIPT" ]; then
    sudo -u osint bash "$BACKUP_SCRIPT" "$DIR/backups" && echo "✅ backup gedaan" || {
        echo "⚠️  backup had warnings"
        OVERALL_STATUS="failed"
    }
    LATEST_BACKUP=$(find "$DIR/backups" -maxdepth 1 -name "iveras_backup_*.tar.gz.gpg" -type f 2>/dev/null | sort -r | head -1)
else
    echo "⚠️  backup.sh niet gevonden — sla backup over"
fi

# --- 2/5 Dependencies ---
echo "=== 2/5 Nieuwe dependencies installeren ==="
sudo -u osint "$VENV_PYTHON" -m pip install -r "$DIR/requirements.txt" --quiet || {
    echo "❌ pip install mislukt"
    OVERALL_STATUS="failed"
}

# --- 3/5 Git pull ---
echo "=== 3/5 Code ophalen ==="
cd "$DIR"
sudo -u osint git pull origin "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo master)" || {
    echo "❌ git pull mislukt"
    OVERALL_STATUS="failed"
}

# --- 4/5 Database migraties ---
echo "=== 4/5 Database migraties draaien ==="
if [ -n "$DB_URL" ]; then
    sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic upgrade head || {
        echo "❌ alembic upgrade mislukt"
        OVERALL_STATUS="failed"
    }
else
    echo "⚠️  Geen DATABASE_URL in .env — valt terug op SQLite"
    sudo -u osint "$VENV_PYTHON" -m alembic upgrade head || {
        echo "❌ alembic upgrade mislukt"
        OVERALL_STATUS="failed"
    }
fi

# --- 5/5 Restart ---
echo "=== 5/5 Server herstarten ==="
sudo systemctl restart osint-dashboard || {
    echo "❌ herstart mislukt"
    OVERALL_STATUS="failed"
}

echo ""

# --- Notificatie ---
echo "=== Notificatie ==="
if [ -f "$NOTIFY_SCRIPT" ]; then
    sudo -u osint "$VENV_PYTHON" "$NOTIFY_SCRIPT" \
        --dir "$DIR" \
        --status "$OVERALL_STATUS" \
        --backup "$LATEST_BACKUP" || echo "⚠️  notificatie mislukt (wordt alleen gelogd)"
    echo "✅ notificatie verstuurd"
else
    echo "⚠️  notify_update.py niet gevonden — email overgeslagen"
fi

echo ""
if [ "$OVERALL_STATUS" = "success" ]; then
    echo "🎉 Update compleet! Controleer met: sudo systemctl status osint-dashboard"
    exit 0
else
    echo "❌ Update mislukt — controleer de output hierboven."
    echo "   Herstel mogelijk met: sudo -u osint $DIR/scripts/restore.sh"
    exit 1
fi
