#!/usr/bin/env bash
# Verify a backup by restoring it to an isolated PostgreSQL database.
# This script never writes to the production database or production uploads.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_INPUT="${1:-${BACKUP_DIR:-$SCRIPT_DIR/backups}}"
REPORT_DIR="${DR_REPORT_DIR:-$SCRIPT_DIR/reports/dr}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/iveras-dr-verify.XXXXXX")"
CHECKS_FILE="$WORK_DIR/checks.tsv"
REPORT_PATH="$REPORT_DIR/dr-verification-$TIMESTAMP.json"
BACKUP_ID="unknown"
ERRORS=0
REPORT_WRITTEN=false
CREATED_DB=""
COUNTS_JSON='{}'
PYTHON="${DR_PYTHON:-$SCRIPT_DIR/venv/bin/python3}"
if [ ! -x "$PYTHON" ]; then
    PYTHON=python3
fi

# Structural DR config: libpq connection settings for the isolated restore
# (PGSERVICE/PGSERVICEFILE/PGPASSFILE) are created by dr_setup.sh in
# /etc/default/osint-dr. Source it when no explicit connection env is set so a
# bare invocation stays green instead of failing only on database_restore.
if [ -z "${DR_VERIFY_DATABASE_URL:-}" ] && [ -z "${PGSERVICE:-}" ] && [ -z "${PGHOST:-}" ] && [ -f /etc/default/osint-dr ]; then
    set -a
    # shellcheck disable=SC1091
    . /etc/default/osint-dr
    set +a
fi

record() {
    local name="$1"
    local status="$2"
    local detail="$3"
    printf '%s\t%s\t%s\n' "$name" "$status" "$detail" >> "$CHECKS_FILE"
    if [ "$status" != "pass" ]; then
        ERRORS=$((ERRORS + 1))
    fi
}

finish() {
    local rc=$?
    if [ "$REPORT_WRITTEN" = false ]; then
        set +e
        python3 "$SCRIPT_DIR/scripts/dr_report.py" \
            --output "$REPORT_PATH" \
            --backup-id "$BACKUP_ID" \
            --commit-sha "$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || printf unknown)" \
            --checks-file "$CHECKS_FILE" \
            --counts-json "$COUNTS_JSON" >/dev/null 2>&1
        REPORT_WRITTEN=true
        set -e
    fi
    if [ -n "$CREATED_DB" ] && {
        [ -n "${DR_VERIFY_DATABASE_URL:-}" ] || [ -n "${PGSERVICE:-}" ] || [ -n "${PGHOST:-}" ];
    }; then
        "$PYTHON" "$SCRIPT_DIR/scripts/dr_postgres.py" drop \
            --database "$CREATED_DB" \
            >/dev/null 2>&1 || true
    fi
    rm -rf "$WORK_DIR"
    exit "$rc"
}
trap finish EXIT

mkdir -p "$REPORT_DIR"
: > "$CHECKS_FILE"

if [ "$BACKUP_INPUT" = "--cleanup" ]; then
    find "${TMPDIR:-/tmp}" -maxdepth 1 -name "iveras-dr-verify.*" -type d -mtime +7 -exec rm -rf {} + 2>/dev/null || true
    REPORT_WRITTEN=true
    exit 0
fi

if [ -f "$BACKUP_INPUT" ]; then
    ARCHIVE="$BACKUP_INPUT"
else
    ARCHIVE=""
    while IFS= read -r candidate; do
        ARCHIVE="$candidate"
    done < <(find "$BACKUP_INPUT" -maxdepth 1 -type f \( \
        -name 'iveras_backup_*.tar.gz.gpg' -o -name 'iveras_backup_*.tar.gz' \
    \) -print 2>/dev/null | sort)
fi

if [ -z "$ARCHIVE" ]; then
    record archive fail "no backup archive found"
    exit 1
fi
BACKUP_ID="$(basename "$ARCHIVE")"
BACKUP_ID="${BACKUP_ID%.gpg}"
BACKUP_ID="${BACKUP_ID%.tar.gz}"
record archive pass "selected backup"

# Freshness floor: the verifier must not certify a stale archive. Default to
# rejecting archives older than 24h; override with DR_MAX_ARCHIVE_AGE (seconds).
MAX_ARCHIVE_AGE="${DR_MAX_ARCHIVE_AGE:-86400}"
if [[ "$MAX_ARCHIVE_AGE" =~ ^[0-9]+$ ]] && [ -f "$ARCHIVE" ]; then
    ARCHIVE_AGE="$(( $(date +%s) - $(stat -c %Y "$ARCHIVE") ))"
    if [ "$ARCHIVE_AGE" -gt "$MAX_ARCHIVE_AGE" ]; then
        record freshness fail "archive is $ARCHIVE_AGE s old (max $MAX_ARCHIVE_AGE s)"
        exit 2
    fi
    record freshness pass "archive is $ARCHIVE_AGE s old (max $MAX_ARCHIVE_AGE s)"
fi

if [[ "$ARCHIVE" == *.gpg ]]; then
    KEY_FILE="${DR_BACKUP_KEY_FILE:-$(dirname "$ARCHIVE")/backup-key.gpg}"
    if [ ! -f "$KEY_FILE" ]; then
        record decrypt fail "backup encryption key is missing"
        exit 2
    fi
    if ! gpg --decrypt --batch --passphrase-file "$KEY_FILE" \
        --output "$WORK_DIR/backup.tar.gz" "$ARCHIVE" >/dev/null 2>&1; then
        record decrypt fail "encrypted archive could not be decrypted"
        exit 2
    fi
    ARCHIVE_TO_EXTRACT="$WORK_DIR/backup.tar.gz"
else
    ARCHIVE_TO_EXTRACT="$ARCHIVE"
fi

if ! tar xzf "$ARCHIVE_TO_EXTRACT" -C "$WORK_DIR" >/dev/null 2>&1; then
    record extract fail "archive could not be extracted"
    exit 2
fi
EXTRACT_DIR=""
while IFS= read -r candidate; do
    EXTRACT_DIR="$candidate"
    break
done < <(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d -print)
if [ -z "$EXTRACT_DIR" ]; then
    record extract fail "archive contains no backup directory"
    exit 2
fi
record extract pass "archive extracted to isolated temporary directory"

if [ ! -f "$EXTRACT_DIR/database.sql.gz" ]; then
    record database_restore fail "database.sql.gz is missing"
    exit 2
fi
if ! gunzip -c "$EXTRACT_DIR/database.sql.gz" > "$WORK_DIR/database.sql" 2>/dev/null; then
    record database_restore fail "database dump is not valid gzip"
    exit 2
fi

DR_VERIFY_DATABASE_URL="${DR_VERIFY_DATABASE_URL:-}"
if [ -z "$DR_VERIFY_DATABASE_URL" ] && [ -z "${PGSERVICE:-}" ] && [ -z "${PGHOST:-}" ]; then
    record database_restore fail "DR_VERIFY_DATABASE_URL or libpq connection settings are not configured"
else
    CREATED_DB="iveras_dr_${TIMESTAMP//[^A-Za-z0-9_]/}_${RANDOM}"
    if ! "$PYTHON" "$SCRIPT_DIR/scripts/dr_postgres.py" create \
        --database "$CREATED_DB" \
        >/dev/null 2>&1; then
        record database_restore fail "isolated PostgreSQL database could not be created"
        exit 2
    fi
    if ! "$PYTHON" "$SCRIPT_DIR/scripts/dr_postgres.py" restore \
        --database "$CREATED_DB" --sql-file "$WORK_DIR/database.sql" \
        >/dev/null 2>"$WORK_DIR/restore.err"; then
        record database_restore fail "database dump could not be restored"
        exit 2
    fi
    record database_restore pass "restored to isolated database"

    query_count() {
        "$PYTHON" "$SCRIPT_DIR/scripts/dr_postgres.py" query --database "$CREATED_DB" \
            --sql "SELECT count(*) FROM $1" 2>/dev/null
    }
    TENANTS_COUNT="$(query_count tenants || true)"
    CASES_COUNT="$(query_count cases || true)"
    USERS_COUNT="$(query_count users || true)"
    if [[ "$TENANTS_COUNT" =~ ^[0-9]+$ && "$CASES_COUNT" =~ ^[0-9]+$ && "$USERS_COUNT" =~ ^[0-9]+$ ]]; then
        record row_counts pass "tenants=$TENANTS_COUNT cases=$CASES_COUNT users=$USERS_COUNT"
        COUNTS_JSON="{\"tenants\":$TENANTS_COUNT,\"cases\":$CASES_COUNT,\"users\":$USERS_COUNT}"
    else
        record row_counts fail "tenant/case/user counts could not be read"
        COUNTS_JSON='{}'
    fi

    MIGRATION_VERSION="$("$PYTHON" "$SCRIPT_DIR/scripts/dr_postgres.py" query \
        --database "$CREATED_DB" --sql \
        "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || true)"
    EXPECTED_HEAD="${DR_EXPECTED_ALEMBIC_HEAD:-}"
    if [ -z "$EXPECTED_HEAD" ] && command -v alembic >/dev/null 2>&1; then
        EXPECTED_HEAD="$(cd "$SCRIPT_DIR" && alembic heads 2>/dev/null | while read -r head _; do printf '%s' "$head"; break; done)"
    fi
    if [ -n "$MIGRATION_VERSION" ] && { [ -z "$EXPECTED_HEAD" ] || [ "$MIGRATION_VERSION" = "$EXPECTED_HEAD" ]; }; then
        record migration_version pass "$MIGRATION_VERSION"
    else
        record migration_version fail "restored=$MIGRATION_VERSION expected=${EXPECTED_HEAD:-configured-head-required}"
    fi

    if [ -n "${CMS_ENCRYPTION_KEY:-}" ]; then
        export CMS_ENCRYPTION_KEY
    elif [ -f "$EXTRACT_DIR/env.txt" ]; then
        CMS_ENCRYPTION_KEY="$(python3 - "$EXTRACT_DIR/env.txt" <<'PY'
import sys
for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("CMS_ENCRYPTION_KEY="):
        print(line.rstrip().split("=", 1)[1].strip().strip("'\""))
        break
PY
        )"
        export CMS_ENCRYPTION_KEY
    fi
    ENCRYPTED_CHECK="$(CMS_ENCRYPTION_KEY="${CMS_ENCRYPTION_KEY:-}" \
        "$PYTHON" "$SCRIPT_DIR/scripts/dr_postgres.py" encrypted-check \
        --database "$CREATED_DB" 2>/dev/null || true)"
    if [[ "$ENCRYPTED_CHECK" == *'"status": "pass"'* ]]; then
        record encrypted_fields pass "$ENCRYPTED_CHECK"
    else
        record encrypted_fields fail "restored encrypted field could not be read"
    fi
fi

UPLOAD_ENTRY=""
if [ -f "$EXTRACT_DIR/uploads.tar.gz" ]; then
    UPLOAD_ENTRY="$(tar tzf "$EXTRACT_DIR/uploads.tar.gz" 2>/dev/null | \
        awk 'NF && $0 !~ /\/$/ { print; exit }' || true)"
fi
if [ -n "$UPLOAD_ENTRY" ]; then
    record uploads pass "uploads archive contains files"
else
    record uploads fail "uploads archive is missing or empty"
fi

if [ -f "$EXTRACT_DIR/license.db" ] && python3 - "$EXTRACT_DIR/license.db" <<'PY'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
connection.close()
PY
then
    record license_database pass "SQLite integrity and schema checks passed"
else
    record license_database fail "license-server data/license.db is missing or unreadable"
fi

if [ -f "$EXTRACT_DIR/license-private.pem" ] && openssl pkey -in "$EXTRACT_DIR/license-private.pem" -check -noout >/dev/null 2>&1; then
    record license_private_key pass "Ed25519 private key is readable and valid"
else
    record license_private_key fail "Ed25519 private key is missing or invalid"
fi

set +e
python3 "$SCRIPT_DIR/scripts/dr_report.py" \
    --output "$REPORT_PATH" \
    --backup-id "$BACKUP_ID" \
    --commit-sha "$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || printf unknown)" \
    --checks-file "$CHECKS_FILE" \
    --counts-json "$COUNTS_JSON" >/dev/null 2>&1
REPORT_RC=$?
REPORT_WRITTEN=true
set -e
printf 'DR report: %s\n' "$REPORT_PATH"
if [ "$ERRORS" -gt 0 ] || [ "$REPORT_RC" -ne 0 ]; then
    exit 2
fi
exit 0
