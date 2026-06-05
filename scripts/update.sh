#!/usr/bin/env bash
set -euo pipefail

DIR="/opt/osint-dashboard"
VENV_PYTHON="$DIR/venv/bin/python3"

if [ ! -d "$DIR" ]; then
    echo "❌ $DIR bestaat niet — ben je op de productie-server?"
    exit 1
fi
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Geen venv gevonden op $VENV_PYTHON"
    exit 1
fi

echo "=== 1/4 Nieuwe dependencies installeren ==="
sudo -u osint "$VENV_PYTHON" -m pip install -r "$DIR/requirements.txt" --quiet
echo "✅ pip install done"

echo "=== 2/4 Code ophalen ==="
cd "$DIR"
sudo -u osint git pull origin master
echo "✅ git pull done"

echo "=== 3/4 Database migraties draaien ==="
sudo -u osint "$VENV_PYTHON" -m alembic upgrade head
echo "✅ alembic done"

echo "=== 4/4 Server herstarten ==="
sudo systemctl restart osint-dashboard
echo "✅ osint-dashboard herstart"

echo ""
echo "🎉 Update compleet! Controleer met: sudo systemctl status osint-dashboard"
