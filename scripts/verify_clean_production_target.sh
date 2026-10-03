#!/usr/bin/env bash
# Read-only post-import validator for the clean production target.
# It never deletes data and never prints secret values.
set -euo pipefail

DB_USER="${MIGRATION_DB_USER:-cms}"
DB_NAME="${MIGRATION_DB_NAME:-cms_db}"
EXPECTED_EMAIL="${MIGRATION_EXPECTED_EMAIL:-ivan.versteegh@protonmail.com}"
EXPECTED_TENANT="${MIGRATION_EXPECTED_TENANT:-Default Organization}"

psql() {
    docker compose exec -T postgres psql -X -U "$DB_USER" -d "$DB_NAME" "$@" </dev/null
}

failures=0
check_equals() {
    local label="$1" actual="$2" expected="$3"
    if [[ "$actual" == "$expected" ]]; then
        printf 'CHECK\t%-36s OK\t%s\n' "$label" "$actual"
    else
        printf 'CHECK\t%-36s FAIL\tgot=%s expected=%s\n' "$label" "$actual" "$expected"
        failures=$((failures + 1))
    fi
}

scalar() {
    psql -At -c "$1" | tr -d '\r' | tail -n 1
}

echo "CLEAN_PRODUCTION_TARGET_VALIDATION=1"
echo "MODE=read-only"
echo "EXPECTED_EMAIL=$EXPECTED_EMAIL"
echo "EXPECTED_TENANT=$EXPECTED_TENANT"
echo

check_equals "tenant_count" \
    "$(scalar 'SELECT count(*) FROM tenants;')" "1"
check_equals "expected_tenant_count" \
    "$(scalar "SELECT count(*) FROM tenants WHERE name = '$EXPECTED_TENANT';")" "1"
check_equals "user_count" \
    "$(scalar 'SELECT count(*) FROM users;')" "1"
check_equals "expected_user_count" \
    "$(scalar "SELECT count(*) FROM users WHERE lower(email) = lower('$EXPECTED_EMAIL');")" "1"

for table in clients subjects cases investigations research_actions findings documents screenshots; do
    check_equals "${table}_count" \
        "$(scalar "SELECT count(*) FROM ${table};")" "0"
done

check_equals "api_keys_count" \
    "$(scalar 'SELECT count(*) FROM api_keys;')" "0"

echo
if (( failures == 0 )); then
    echo "CLEAN_PRODUCTION_TARGET=OK"
    exit 0
fi

echo "CLEAN_PRODUCTION_TARGET=NOT_READY"
echo "FAILURES=$failures"
exit 1
