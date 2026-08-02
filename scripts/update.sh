#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — CLI Update Script
# ===========================================
# Runs backup, git pull, pip install, alembic, and restart.
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
    echo "❌ $DIR does not exist — are you on the production server?"
    exit 1
fi
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ No venv found at $VENV_PYTHON"
    exit 1
fi

# Read DATABASE_URL from .env for sudo commands (sudo strips env vars)
DB_URL=""
if [ -f "$ENV_FILE" ]; then
    DB_URL=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)
fi

BACKUP_SCRIPT="$DIR/scripts/backup.sh"
NOTIFY_SCRIPT="$DIR/scripts/notify_update.py"

# --- 1/5 Backup ---
echo "=== 1/5 Creating backup ==="
if [ -f "$BACKUP_SCRIPT" ]; then
    sudo -u osint bash "$BACKUP_SCRIPT" "$DIR/backups" && echo "✅ backup done" || {
        echo "⚠️  backup had warnings"
        OVERALL_STATUS="failed"
    }
    LATEST_BACKUP=$(find "$DIR/backups" -maxdepth 1 -name "iveras_backup_*.tar.gz.gpg" -type f 2>/dev/null | sort -r | head -1)
else
    echo "⚠️  backup.sh not found — skipping backup"
fi

# --- 2/5 Git pull ---
echo "=== 2/5 Pulling latest code ==="
cd "$DIR"
sudo -u osint git pull origin "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo master)" || {
    echo "❌ git pull failed"
    OVERALL_STATUS="failed"
}

# --- 3/6 Dependencies ---
echo "=== 3/6 Installing dependencies ==="
sudo -u osint "$VENV_PYTHON" -m pip install -r "$DIR/requirements.txt" --quiet || {
    echo "❌ pip install failed"
    OVERALL_STATUS="failed"
}

# --- 4/6 Frontend build ---
echo "=== 4/6 Building frontend assets ==="
sudo -u osint env PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    node "$DIR/build.mjs" || {
    echo "❌ frontend build failed"
    OVERALL_STATUS="failed"
}

# --- 5/6 Database migrations ---
echo "=== 5/6 Running database migrations ==="
if [ -n "$DB_URL" ]; then
    sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic upgrade head || {
        echo "❌ alembic upgrade failed"
        OVERALL_STATUS="failed"
    }
else
    echo "⚠️  No DATABASE_URL in .env — falling back to SQLite"
    sudo -u osint "$VENV_PYTHON" -m alembic upgrade head || {
        echo "❌ alembic upgrade failed"
        OVERALL_STATUS="failed"
    }
fi

# --- 6/6 Restart ---
echo "=== 6/6 Restarting server ==="
sudo systemctl restart osint-dashboard || {
    echo "❌ restart failed"
    OVERALL_STATUS="failed"
}

echo ""

# --- Notification ---
echo "=== Notification ==="
if [ -f "$NOTIFY_SCRIPT" ]; then
    sudo -u osint "$VENV_PYTHON" "$NOTIFY_SCRIPT" \
        --dir "$DIR" \
        --status "$OVERALL_STATUS" \
        --backup "$LATEST_BACKUP" || echo "⚠️  notification failed (will be logged only)"
    echo "✅ notification sent"
else
    echo "⚠️  notify_update.py not found — skipping email"
fi

echo ""
if [ "$OVERALL_STATUS" = "success" ]; then
    echo "🎉 Update complete! Check with: sudo systemctl status osint-dashboard"
    exit 0
else
    echo "❌ Update failed — check the output above."
    echo "   Recovery possible with: sudo -u osint $DIR/scripts/restore.sh"
    exit 1
fi
