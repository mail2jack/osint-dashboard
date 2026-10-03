#!/usr/bin/env bash
# Create an encrypted, allowlisted settings export.
# The plaintext SQL exists only in a private temporary directory and is never
# printed. This script does not change the database.
set -euo pipefail

usage() {
    echo "Usage: $0 OUTPUT.gpg PASSPHRASE_FILE" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
OUTPUT=$1
PASSPHRASE_FILE=$2

[[ "$OUTPUT" == *.gpg ]] || {
    echo "OUTPUT must end in .gpg" >&2
    exit 2
}
[[ -f "$PASSPHRASE_FILE" ]] || {
    echo "Passphrase file does not exist" >&2
    exit 2
}
[[ -r "$PASSPHRASE_FILE" ]] || {
    echo "Passphrase file is not readable" >&2
    exit 2
}

umask 077
OUTPUT_DIR=$(dirname -- "$OUTPUT")
mkdir -p -- "$OUTPUT_DIR"
if [[ -e "$OUTPUT" ]]; then
    echo "Refusing to overwrite existing output: $OUTPUT" >&2
    exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
PLAIN="$TMP/allowlisted-settings.sql"
QUERY="$TMP/export.sql"

cat > "$QUERY" <<'SQL'
WITH allowlist(key) AS (VALUES
  ('brave_api_key'),
  ('google_search_api_key'),
  ('overheid_api_key'),
  ('rapidapi_username_key'),
  ('marineplan_api_key'),
  ('pimeyes_api_key'),
  ('tineye_api_key'),
  ('twochat_api_key'),
  ('twochat_whatsapp_number'),
  ('whatsapp_checkleaked_key'),
  ('equasis_email'),
  ('equasis_password'),
  ('picarta_api_key'),
  ('telegram_rapidapi_key'),
  ('openrouter_api_key'),
  ('openrouter_base_url'),
  ('openrouter_model'),
  ('spiderfoot_url'),
  ('spiderfoot_username'),
  ('spiderfoot_password'),
  ('install_id'),
  ('install_token'),
  ('license_public_key'),
  ('license_payload'),
  ('license_signature'),
  ('license_status'),
  ('telemetry_enabled'),
  ('telemetry_server_url'),
  ('telegram_bot_token'),
  ('smtp_server'),
  ('smtp_port'),
  ('smtp_username'),
  ('smtp_password'),
  ('smtp_from_email'),
  ('smtp_from_name'),
  ('twilio_account_sid'),
  ('twilio_auth_token'),
  ('twilio_phone_number'),
  ('twilio_whatsapp_number'),
  ('tor_enabled'),
  ('tor_proxy'),
  ('tor_control_port')
), statements AS (
  SELECT format(
    'INSERT INTO platform_settings (id, key, value, category, description, is_encrypted, created_at, updated_at) VALUES (%L, %L, %L, %L, %L, %L, %L, %L) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, category = EXCLUDED.category, description = EXCLUDED.description, is_encrypted = EXCLUDED.is_encrypted, updated_at = EXCLUDED.updated_at;',
    p.id, p.key, p.value, p.category, p.description, p.is_encrypted,
    p.created_at, p.updated_at
  ) AS sql
  FROM platform_settings p
  JOIN allowlist a ON a.key = p.key
  WHERE p.value IS NOT NULL AND p.value <> ''
  UNION ALL
  SELECT format(
    'INSERT INTO settings (id, key, value, category, description, value_type, options, is_encrypted, is_sensitive, display_order, is_active, created_at, updated_at, created_by) VALUES (%L, %L, %L, %L, %L, %L, %L, %L, %L, %L, %L, %L, %L, NULL) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, category = EXCLUDED.category, description = EXCLUDED.description, value_type = EXCLUDED.value_type, options = EXCLUDED.options, is_encrypted = EXCLUDED.is_encrypted, is_sensitive = EXCLUDED.is_sensitive, display_order = EXCLUDED.display_order, is_active = EXCLUDED.is_active, updated_at = EXCLUDED.updated_at, created_by = NULL;',
    s.id, s.key, s.value, s.category, s.description, s.value_type,
    s.options, s.is_encrypted, s.is_sensitive, s.display_order, s.is_active,
    s.created_at, s.updated_at
  ) AS sql
  FROM settings s
  JOIN allowlist a ON a.key = s.key
  WHERE s.value IS NOT NULL AND s.value <> ''
)
SELECT sql FROM statements ORDER BY sql;
SQL

{
    printf '%s\n' '-- Allowlisted Joost configuration export; values are encrypted in the containing archive.'
    printf '%s\n' 'BEGIN;'
    docker compose exec -T postgres psql -X -U "${MIGRATION_DB_USER:-cms}" \
        -d "${MIGRATION_DB_NAME:-cms_db}" -At -f /dev/stdin < "$QUERY"
    printf '%s\n' 'COMMIT;'
} > "$PLAIN"

gpg --batch --yes --pinentry-mode loopback \
    --passphrase-file "$PASSPHRASE_FILE" \
    --symmetric --cipher-algo AES256 \
    --output "$OUTPUT" "$PLAIN"

rm -f -- "$PLAIN"
echo "ALLOWLISTED_SETTINGS_EXPORT=OK"
echo "OUTPUT=$OUTPUT"
