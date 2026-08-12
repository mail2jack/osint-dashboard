#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Preflight Check Script
# ===============================================
# Read-only gate that must pass BEFORE any deploy/update on the production VPS.
# Orchestrates doctor.py (config/health), DB reachability, Alembic migration
# sync and dependency vulnerability scanning.
#
# Never mutates the system and never prints secret values (only key names).
#
# Usage (as root, on the VPS):
#   /opt/osint-dashboard/scripts/preflight.sh           # full gate
#   /opt/osint-dashboard/scripts/preflight.sh --quick   # skip pip-audit
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — required tooling missing (pip-audit)
#
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
ENV_FILE="$APP_DIR/.env"
FAILED=0
QUICK=0

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=1 ;;
        -h | --help)
            sed -n '2,22p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: onbekend argument '$arg'" >&2
            exit 2
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
if [ ! -f "$ENV_FILE" ]; then
    echo "FAIL: $ENV_FILE ontbreekt" >&2
    exit 1
fi

echo "== 1/4 .env vereiste sleutels aanwezig (waarden worden niet getoond) =="
for key in FLASK_ENV DATABASE_URL CMS_ENCRYPTION_KEY SECRET_KEY; do
    if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
        echo "  OK: $key"
    else
        echo "  FAIL: $key ontbreekt in .env"
        FAILED=1
    fi
done
flask_env=$(grep -m1 -E "^FLASK_ENV=" "$ENV_FILE" | cut -d= -f2- | tr -d ' "' || true)
if [ "$flask_env" = "production" ]; then
    echo "  OK: FLASK_ENV=production"
else
    echo "  FAIL: FLASK_ENV=${flask_env:-<leeg>} (moet production zijn)"
    FAILED=1
fi

echo "== 2/4 doctor.py (diagnostisch — er wordt niets gewijzigd) =="
if sudo python3 "$APP_DIR/scripts/doctor.py" --dry-run; then
    echo "  OK: doctor.py --dry-run"
else
    echo "  FAIL: doctor.py meldt problemen — draai eerst doctor.py zonder --dry-run"
    FAILED=1
fi

echo "== 3/4 PostgreSQL bereikbaar =="
if command -v pg_isready >/dev/null 2>&1; then
    db_host=$(grep -m1 -E "^DATABASE_URL=" "$ENV_FILE" \
        | sed -E 's#^DATABASE_URL=[^@]*@([^:/]+).*#\1#' || true)
    case "$db_host" in
        "" | postgres*)
            echo "  WARN: kon host niet uit DATABASE_URL halen — DB-check via doctor.py"
            ;;
        *)
            if pg_isready -h "$db_host" -t 5 >/dev/null 2>&1; then
                echo "  OK: PostgreSQL bereikbaar ($db_host)"
            else
                echo "  FAIL: PostgreSQL niet bereikbaar ($db_host)"
                FAILED=1
            fi
            ;;
    esac
else
    echo "  WARN: pg_isready niet aanwezig — DB-check via doctor.py"
fi

echo "== 4/4 pip-audit (afhankelijkheden) =="
if [ "$QUICK" -eq 1 ]; then
    echo "  SKIP: --quick"
else
    PIP_AUDIT=""
    if [ -x "$APP_DIR/venv/bin/pip-audit" ]; then
        PIP_AUDIT="$APP_DIR/venv/bin/pip-audit"
    elif command -v pip-audit >/dev/null 2>&1; then
        PIP_AUDIT="$(command -v pip-audit)"
    fi
    if [ -z "$PIP_AUDIT" ]; then
        echo "FAIL: pip-audit niet geïnstalleerd (install: sudo -u osint $APP_DIR/venv/bin/pip install pip-audit)" >&2
        exit 2
    fi
    echo "  GEBRUIKT: $PIP_AUDIT"
    if "$PIP_AUDIT" -r "$APP_DIR/requirements-lock.txt" --progress-spinner off; then
        echo "  OK: geen bekende kwetsbaarheden"
    else
        echo "  FAIL: kwetsbaarheden gevonden — blokkeert deploy"
        FAILED=1
    fi
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "PREFLIGHT PASSED — gereed voor deploy."
else
    echo "PREFLIGHT FAILED — los de bovenstaande punten op vóór deploy."
fi
exit "$FAILED"
