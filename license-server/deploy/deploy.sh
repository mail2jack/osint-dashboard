#!/usr/bin/env bash
#
# Iveras License Server — Deploy / Update Script
# ==============================================
# Veilige rsync vanuit de repo naar /opt/license-server, dan venv-herbouw,
# systemd-unit, restart en health check.
#
# WAAROM DIT BESTAAT:
#   Een plain `rsync --delete` wist runtime-bestanden die alleen op de server
#   bestaan: .env, venv/, data/ (registry-SQLite) en keys/ (Ed25519-privé-
#   sleutel). Verlies van keys/private.pem betekent dat er nooit meer nieuwe
#   licenties kunnen worden ondertekend. De excludes hieronder beschermen die
#   bestanden; het venv wordt bewust (opnieuw) aangemaakt wanneer het mist,
#   zodat een weggewist venv zich bij de volgende deploy zelf herstelt.
#
# Usage (als root, op de VPS, vanuit de repo-root):
#   ./license-server/deploy/deploy.sh
#   ./license-server/deploy/deploy.sh /pad/naar/andere/checkout
#
set -euo pipefail

TARGET=/opt/license-server
SRC="${1:-./license-server/}"
SRC="${SRC%/}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: draai als root (sudo su)" >&2
    exit 1
fi
if [[ ! -d "$SRC" ]]; then
    echo "ERROR: bron $SRC niet gevonden — draai vanuit de repo-root" >&2
    exit 1
fi

echo "=== 1/5 Code syncen (veilige rsync --delete) ==="
sudo rsync -a --delete \
    --exclude='.env' \
    --exclude='venv/' \
    --exclude='data/' \
    --exclude='keys/' \
    --exclude='.cache/' \
    --exclude='.gunicorn/' \
    --exclude='__pycache__/' \
    "$SRC/" "$TARGET/"

sudo chown -R license:license "$TARGET"

if [[ ! -f "$TARGET/.env" ]]; then
    echo "WARNING: $TARGET/.env ontbreekt — de server faalt straks fail-fast." >&2
    echo "         Maak hem eerst aan, zie license-server/README.md (Deployment)." >&2
fi

echo "=== 2/5 venv zekerstellen ==="
if [[ ! -x "$TARGET/venv/bin/python3" ]]; then
    echo "venv ontbreekt -> opnieuw aanmaken"
    sudo -u license python3 -m venv "$TARGET/venv"
fi
sudo -u license "$TARGET/venv/bin/pip" install -q -r "$TARGET/requirements.txt"

echo "=== 3/5 systemd-unit installeren ==="
sudo cp "$TARGET/deploy/license-server.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now license-server

echo "=== 4/5 Service herstarten ==="
sudo systemctl restart license-server
sleep 2

echo "=== 5/5 Health check ==="
curl -fsS http://127.0.0.1:8000/health && echo
