#!/bin/bash
#
# docker-up.sh — Iveras OSINT Dashboard in één commando (Docker)
#
# Usage:
#   ./docker-up.sh
#
# Genereert automatisch een .env (indien afwezig), bouwt en start
# de containers, wacht tot de app gezond is en toont de inloggegevens.

set -e

cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Docker is niet geïnstalleerd."
    echo "   Installatie: https://docs.docker.com/engine/install/"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 is nodig om willekeurige keys te genereren."
    exit 1
fi

ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ℹ️  Geen .env gevonden — genereer er een met willekeurige keys..."
    DB_PASSWORD=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
    CMS_ENCRYPTION_KEY=$(python3 -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
    SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
    INSTALL_ID=$(python3 -c "import uuid;print(uuid.uuid4())")
    INSTALL_TOKEN=$(python3 -c "import secrets;print(secrets.token_hex(32))")
    cat > "$ENV_FILE" <<EOF
DB_PASSWORD=$DB_PASSWORD
CMS_ENCRYPTION_KEY=$CMS_ENCRYPTION_KEY
SECRET_KEY=$SECRET_KEY
PORT=5000
INSTALL_ID=$INSTALL_ID
INSTALL_TOKEN=$INSTALL_TOKEN
EOF
    echo "✅ .env gegenereerd (bewaar dit bestand goed — het bevat secrets)."
else
    echo "ℹ️  .env bestaat al — gebruik de bestaande."
fi

echo ""
echo "🚀 Containers bouwen en starten..."
docker compose up -d --build

echo ""
echo "⏳ Wachten tot de app reageert..."
for i in $(seq 1 60); do
    if curl -sf "http://localhost:${PORT:-5000}/health" >/dev/null 2>&1; then
        echo "✅ App is up!"
        APP_UP=1
        break
    fi
    sleep 2
done

if [ -z "${APP_UP:-}" ]; then
    echo "⚠️  App reageert niet binnen de tijd. Controleer met: docker compose logs -f app"
    exit 1
fi

echo ""
echo "==================================================================="
echo "  Iveras OSINT Dashboard draait:"
echo "    URL:      http://localhost:${PORT:-5000}"
echo "    Email:    admin@localhost"
echo "    Password: changeme123  (meteen wijzigen na eerste login!)"
echo "==================================================================="
echo ""
echo "  Handige commando's:"
echo "    Stoppen:  docker compose down"
echo "    Logs:     docker compose logs -f app"
echo "    Herstart: docker compose restart app"
echo "    Update:   docker compose up -d --build"
echo ""
