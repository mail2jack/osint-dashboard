#!/usr/bin/env bash
# Guided one-time setup for the DR and read-only production snapshot accounts.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/osint-dashboard}"
DR_ROLE="iveras_dr"
SNAPSHOT_ROLE="iveras_snapshot"
SERVICE_FILE="/home/osint/.pg_service_dr.conf"
PASS_FILE="/home/osint/.pgpass-iveras-dr"
ENV_FILE="/etc/default/osint-dr"
CONFIRM=false
DRY_RUN=false

usage() {
    cat <<'EOF'
Usage: dr_setup.sh [--dry-run | --confirm SETUP-DR]

  --dry-run                 Check prerequisites without creating roles/files.
  --confirm SETUP-DR        Create the DR/read-only snapshot setup.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --confirm) [ "${2:-}" = "SETUP-DR" ] || { usage >&2; exit 2; }; CONFIRM=true; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    printf 'Run dit script als root op de VPS.\n' >&2
    exit 2
fi
if [ ! -f "$APP_DIR/.env" ] || [ ! -x "$APP_DIR/venv/bin/python3" ]; then
    printf 'Applicatie of venv ontbreekt in %s.\n' "$APP_DIR" >&2
    exit 2
fi
if ! command -v psql >/dev/null 2>&1; then
    printf 'psql ontbreekt op de VPS.\n' >&2
    exit 2
fi

DB_INFO="$(sudo -u osint bash -lc '
set -a
. /opt/osint-dashboard/.env
set +a
/opt/osint-dashboard/venv/bin/python3 - <<"PY"
from sqlalchemy.engine import make_url
import os
url = make_url(os.environ["DATABASE_URL"])
print("{}\t{}\t{}\t{}".format(url.database, url.username, url.host or "127.0.0.1", url.port or 5432))
PY
')"
IFS=$'\t' read -r DB_NAME DB_OWNER DB_HOST DB_PORT <<< "$DB_INFO"

printf 'Database: %s\nDatabase owner: %s\nPostgreSQL host: %s:%s\n' \
    "$DB_NAME" "$DB_OWNER" "$DB_HOST" "$DB_PORT"
printf 'DR role: %s; snapshot role: %s\n' "$DR_ROLE" "$SNAPSHOT_ROLE"

for role in "$DR_ROLE" "$SNAPSHOT_ROLE"; do
    if sudo -u postgres psql -d postgres -Atqc \
        "SELECT 1 FROM pg_roles WHERE rolname = '$role'" | grep -q '^1$'; then
        printf 'Role bestaat al: %s. Stop voor veiligheid.\n' "$role" >&2
        exit 2
    fi
done

if [ "$DRY_RUN" = true ]; then
    printf 'DRY RUN PASSED: er worden geen rollen, bestanden of rechten gewijzigd.\n'
    exit 0
fi
if [ "$CONFIRM" != true ]; then
    printf 'Gebruik --confirm SETUP-DR voor de eenmalige setup.\n' >&2
    exit 2
fi

DR_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
SNAPSHOT_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
SQL_FILE="$(mktemp)"
chmod 600 "$SQL_FILE"
trap 'rm -f "$SQL_FILE"' EXIT
cat > "$SQL_FILE" <<SQL
CREATE ROLE $DR_ROLE LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD '$DR_PASSWORD';
CREATE ROLE $SNAPSHOT_ROLE LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS PASSWORD '$SNAPSHOT_PASSWORD';
SQL
chown postgres:postgres "$SQL_FILE"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres -f "$SQL_FILE" >/dev/null

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<SQL >/dev/null
GRANT CONNECT ON DATABASE "$DB_NAME" TO $SNAPSHOT_ROLE;
GRANT USAGE ON SCHEMA public TO $SNAPSHOT_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO $SNAPSHOT_ROLE;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO $SNAPSHOT_ROLE;
ALTER DEFAULT PRIVILEGES FOR ROLE "$DB_OWNER" IN SCHEMA public GRANT SELECT ON TABLES TO $SNAPSHOT_ROLE;
ALTER DEFAULT PRIVILEGES FOR ROLE "$DB_OWNER" IN SCHEMA public GRANT SELECT ON SEQUENCES TO $SNAPSHOT_ROLE;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM $DR_ROLE;
REVOKE ALL ON SCHEMA public FROM $DR_ROLE;
SQL
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
    "GRANT CONNECT ON DATABASE postgres TO $DR_ROLE" >/dev/null

cat > "$SERVICE_FILE" <<EOF
[iveras-dr]
host=$DB_HOST
port=$DB_PORT
user=$DR_ROLE
dbname=postgres

[iveras-production-readonly]
host=$DB_HOST
port=$DB_PORT
user=$SNAPSHOT_ROLE
dbname=$DB_NAME
EOF
chown osint:osint "$SERVICE_FILE"
chmod 600 "$SERVICE_FILE"

cat > "$PASS_FILE" <<EOF
$DB_HOST:$DB_PORT:*:$DR_ROLE:$DR_PASSWORD
$DB_HOST:$DB_PORT:$DB_NAME:$SNAPSHOT_ROLE:$SNAPSHOT_PASSWORD
EOF
chown osint:osint "$PASS_FILE"
chmod 600 "$PASS_FILE"
unset DR_PASSWORD SNAPSHOT_PASSWORD

cat > "$ENV_FILE" <<EOF
PGSERVICEFILE=$SERVICE_FILE
PGPASSFILE=$PASS_FILE
PGSERVICE=iveras-dr
DR_PRODUCTION_PGSERVICE=iveras-production-readonly
DR_PRODUCTION_UPLOAD_DIR=$APP_DIR/static/uploads
DR_DRILL_EVIDENCE_DIR=$APP_DIR/reports/dr-drill
EOF
chown osint:osint "$ENV_FILE"
chmod 600 "$ENV_FILE"

sudo -u osint bash -lc "set -a; . '$ENV_FILE'; set +a; psql -Atqc 'SELECT 1'"
if sudo -u postgres psql -d "$DB_NAME" -Atqc \
    "SELECT has_table_privilege('$DR_ROLE', 'public.cases', 'INSERT'), has_table_privilege('$SNAPSHOT_ROLE', 'public.cases', 'SELECT')" \
    | grep -q '^f|t$'; then
    printf 'DR setup geslaagd: DR-account kan geen cases schrijven; snapshot-account kan lezen.\n'
else
    printf 'DR privilege check mislukt.\n' >&2
    exit 1
fi

printf '\nGebruik voor de drill:\n'
printf "sudo -u osint bash -lc 'set -a; . %s; set +a; /opt/osint-dashboard/scripts/dr_production_gate.sh before --operator Alice --evidence-dir %s'\n" "$ENV_FILE" "$APP_DIR/reports/dr-drill"
