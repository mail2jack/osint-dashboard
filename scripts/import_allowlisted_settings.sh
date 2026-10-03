#!/usr/bin/env bash
# Validate or apply an encrypted allowlisted settings export.
# Default mode is validation only. Database writes require --apply.
set -euo pipefail

usage() {
    echo "Usage: $0 [--apply] BUNDLE.gpg PASSPHRASE_FILE" >&2
    exit 2
}

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
    APPLY=1
    shift
fi
[[ $# -eq 2 ]] || usage
BUNDLE=$1
PASSPHRASE_FILE=$2

[[ -f "$BUNDLE" ]] || { echo "Bundle does not exist" >&2; exit 2; }
[[ -f "$PASSPHRASE_FILE" ]] || { echo "Passphrase file does not exist" >&2; exit 2; }

umask 077
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
PLAIN="$TMP/allowlisted-settings.sql"

gpg --batch --yes --pinentry-mode loopback \
    --passphrase-file "$PASSPHRASE_FILE" \
    --decrypt --output "$PLAIN" "$BUNDLE" >/dev/null

grep -q '^BEGIN;$' "$PLAIN" || {
    echo "Bundle is missing the expected transaction marker" >&2
    exit 1
}
grep -q '^COMMIT;$' "$PLAIN" || {
    echo "Bundle is missing the expected commit marker" >&2
    exit 1
}

if grep -Eiq '^(DROP|DELETE|TRUNCATE|ALTER|CREATE)[[:space:]]' "$PLAIN"; then
    echo "Bundle contains a forbidden destructive/schema statement" >&2
    exit 1
fi

TABLES=$(awk '/^INSERT INTO / {print $3}' "$PLAIN" | sort -u)
if [[ -z "$TABLES" ]]; then
    echo "Bundle contains no settings statements" >&2
    exit 1
fi
if printf '%s\n' "$TABLES" | grep -Evq '^(platform_settings|settings)$'; then
    echo "Bundle contains a table outside the settings allowlist" >&2
    exit 1
fi

STATEMENTS=$(grep -c '^INSERT INTO ' "$PLAIN" || true)
echo "ALLOWLISTED_SETTINGS_BUNDLE=VALID"
echo "MODE=$([[ $APPLY -eq 1 ]] && echo apply || echo check)"
echo "STATEMENTS=$STATEMENTS"
echo "TABLES=$(printf '%s' "$TABLES" | tr '\n' ', ' | sed 's/[, ]*$//')"

if (( APPLY == 0 )); then
    echo "DATABASE_CHANGED=NO"
    exit 0
fi

docker compose exec -T postgres psql -X -U "${MIGRATION_DB_USER:-cms}" \
    -d "${MIGRATION_DB_NAME:-cms_db}" -v ON_ERROR_STOP=1 \
    -f /dev/stdin < "$PLAIN"

echo "DATABASE_CHANGED=YES"
