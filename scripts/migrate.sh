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
# The helper function below is source-friendly: tests `source` this file
# (skipping the main flow via the BASH_SOURCE guard) to exercise run_migrate
# with a stub python (PYTHON_BIN) — no sudo, no server, no database touched.
# =============================================================================

set -euo pipefail

# ---------- Sourceable migration step ----------
# Runs `alembic upgrade head` exactly once, read-only, fail-closed.
# PYTHON_BIN can be overridden from the environment (tests inject a stub).
# This function intentionally does NOT call sudo — the BASH_SOURCE main flow
# below wraps it with sudo -u osint when run as root in production.
run_migrate() {
    local app_dir="${APP_DIR:-/opt/osint-dashboard}"
    local pybin="${PYTHON_BIN:-$app_dir/venv/bin/python3}"
    local env_file="$app_dir/.env"

    if [ ! -f "$pybin" ]; then
        pybin="python3"
    fi
    if [ ! -f "$env_file" ]; then
        echo "ERROR: $env_file ontbreekt" >&2
        return 1
    fi

    cd "$app_dir" || { echo "ERROR: cd $app_dir mislukt" >&2; return 1; }

    local db_url
    db_url="$(grep -m1 '^DATABASE_URL=' "$env_file" | cut -d= -f2- || true)"

    if [ -n "$db_url" ]; then
        if ! env DATABASE_URL="$db_url" "$pybin" -m alembic upgrade head; then
            echo "ERROR: alembic upgrade head mislukt" >&2
            return 1
        fi
    else
        echo "WARNING: geen DATABASE_URL in .env — SQLite fallback"
        if ! "$pybin" -m alembic upgrade head; then
            echo "ERROR: alembic upgrade head mislukt" >&2
            return 1
        fi
    fi
}

# ---------- Main production flow ----------
# Skipped when this file is `source`d by tests (see BASH_SOURCE check).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

    sudo -v || exit 1

    _MIGRATE_APP_DIR="${APP_DIR:-/opt/osint-dashboard}"
    _MIGRATE_ENV="$_MIGRATE_APP_DIR/.env"
    _MIGRATE_VENV="$_MIGRATE_APP_DIR/venv/bin/python3"

    if [ ! -d "$_MIGRATE_APP_DIR" ]; then
        echo "ERROR: $_MIGRATE_APP_DIR bestaat niet" >&2
        exit 1
    fi
    if [ ! -f "$_MIGRATE_ENV" ]; then
        echo "ERROR: $_MIGRATE_ENV ontbreekt" >&2
        exit 1
    fi
    if [ ! -x "$_MIGRATE_VENV" ]; then
        echo "ERROR: geen venv gevonden op $_MIGRATE_VENV" >&2
        exit 1
    fi

    echo "==== migrate.sh start $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
    echo "Schema migratie naar head (gecontroleerde deploy-flow)..."
    sudo -u osint env APP_DIR="$_MIGRATE_APP_DIR" \
        PYTHON_BIN="${PYTHON_BIN:-$_MIGRATE_VENV}" \
        bash -c "source \"$(realpath "$0")\" && run_migrate"
    echo "==== migrate.sh klaar $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="

fi