#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Controlled Schema Migration
# =====================================================
# The ONLY intended entry points that run Alembic DDL are this script and step
# 6/8 of scripts/update.sh. A normal app- or timer-start NEVER migrates (P0
# after incident 20260909): the app only runs a read-only schema sync-check.
#
# Usage (as root, on the production server):
#   sudo ./scripts/migrate.sh
#   sudo APP_DIR=/opt/osint-dashboard ./scripts/migrate.sh
#
sudo -v || exit 1

APP_DIR="${APP_DIR:-/opt/osint-dashboard}"
ENV_FILE="$APP_DIR/.env"
VENV_PYTHON="$APP_DIR/venv/bin/python3"

if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: $APP_DIR bestaat niet" >&2
    exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE ontbreekt" >&2
    exit 1
fi
if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: geen venv gevonden op $VENV_PYTHON" >&2
    exit 1
fi

DB_URL=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)
echo "==== migrate.sh start $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
echo "Schema migratie naar head (gecontroleerde deploy-flow)..."
cd "$APP_DIR" || { echo "ERROR: cd $APP_DIR mislukt" >&2; exit 1; }
if [ -n "$DB_URL" ]; then
    sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic upgrade head
else
    echo "WARNING: geen DATABASE_URL in .env — SQLite fallback"
    sudo -u osint "$VENV_PYTHON" -m alembic upgrade head
fi
echo "==== migrate.sh klaar $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="