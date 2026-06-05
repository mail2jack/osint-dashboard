#!/usr/bin/env bash
set -euo pipefail

DIR="/opt/osint-dashboard"
VENV_PYTHON="$DIR/venv/bin/python3"
ENV_FILE="$DIR/.env"

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

echo "=== 1/4 Nieuwe dependencies installeren ==="
sudo -u osint "$VENV_PYTHON" -m pip install -r "$DIR/requirements.txt" --quiet
echo "✅ pip install done"

echo "=== 2/4 Code ophalen ==="
cd "$DIR"
sudo -u osint git pull origin master
echo "✅ git pull done"

echo "=== 3/4 Database migraties draaien ==="
if [ -n "$DB_URL" ]; then
    sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic upgrade head
else
    echo "⚠️  Geen DATABASE_URL in .env — valt terug op SQLite"
    sudo -u osint "$VENV_PYTHON" -m alembic upgrade head
fi
echo "✅ alembic done"

echo "=== 4/4 Server herstarten ==="
sudo systemctl restart osint-dashboard
echo "✅ osint-dashboard herstart"

echo ""
echo "🎉 Update compleet! Controleer met: sudo systemctl status osint-dashboard"
