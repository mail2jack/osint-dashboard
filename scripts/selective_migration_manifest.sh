#!/usr/bin/env bash
# Read-only manifest for the clean-production migration.
# This script never prints setting values and never mutates the database.
set -euo pipefail

DB_USER="${MIGRATION_DB_USER:-cms}"
DB_NAME="${MIGRATION_DB_NAME:-cms_db}"

psql() {
    docker compose exec -T postgres psql -X -U "$DB_USER" -d "$DB_NAME" "$@" </dev/null
}

echo "SELECTIVE_MIGRATION_MANIFEST=1"
echo "MODE=read-only"
echo "GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "[SOURCE_COUNTS]"
psql -AtF $'\t' -c "
SELECT 'tenants', count(*) FROM tenants
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'clients', count(*) FROM clients
UNION ALL SELECT 'subjects', count(*) FROM subjects
UNION ALL SELECT 'cases', count(*) FROM cases
UNION ALL SELECT 'investigations', count(*) FROM investigations
UNION ALL SELECT 'research_actions', count(*) FROM research_actions
UNION ALL SELECT 'findings', count(*) FROM findings
UNION ALL SELECT 'documents', count(*) FROM documents
UNION ALL SELECT 'api_keys', count(*) FROM api_keys
ORDER BY 1;"
echo

echo "[CONFIGURATION_TABLES]"
psql -AtF $'\t' -c "
SELECT 'platform_settings', count(*) FROM platform_settings
UNION ALL SELECT 'settings', count(*) FROM settings
UNION ALL SELECT 'tenant_settings', count(*) FROM tenant_settings;"
echo

echo "[PROPOSED_ALLOWLIST_METADATA_ONLY]"
psql -AtF $'\t' -c "
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
), metadata AS (
  SELECT a.key,
         CASE WHEN p.key IS NOT NULL THEN 'platform_settings'
              WHEN s.key IS NOT NULL THEN 'settings'
              ELSE 'missing' END AS source,
         COALESCE(p.is_encrypted, s.is_encrypted, false) AS is_encrypted,
         COALESCE(s.is_sensitive, false) AS is_sensitive,
         CASE
           WHEN p.key IS NOT NULL THEN (p.value IS NOT NULL AND p.value <> '')
           WHEN s.key IS NOT NULL THEN (s.value IS NOT NULL AND s.value <> '')
           ELSE false
         END AS has_value
  FROM allowlist a
  LEFT JOIN platform_settings p ON p.key = a.key
  LEFT JOIN settings s ON s.key = a.key
)
SELECT key, source, has_value, is_encrypted, is_sensitive
FROM metadata
ORDER BY key;"
echo

echo "[EXCLUSIONS]"
echo "api_keys\tEXCLUDE_AND_REISSUE"
echo "business_data\tEXCLUDE"
echo "sessions_recovery_codes_tokens\tEXCLUDE"
echo "openrouter\tINCLUDE_EXISTING_VALUE_IF_SECURELY_TRANSFERRED"
echo

echo "MANIFEST_COMPLETE=1"
