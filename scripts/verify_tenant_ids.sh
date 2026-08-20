#!/usr/bin/env bash
# Fase 2: Verificatie tenant_id in audit_logs en login_logs
# Gebruik: ./scripts/verify_tenant_ids.sh
# Vereist: psql-toegang met geautoriseerde DB-rol (niet de app-rol)
set -euo pipefail

DB="${DATABASE_URL:-postgresql:///cms_prod}"

echo "=== Fase 2: Tenant-ID verificatie ==="
echo "DB: $DB"
echo ""

echo "--- 1. NULL-checks ---"
psql "$DB" -c "
SELECT 'audit_logs NULL count' AS check_name, count(*) AS result
FROM audit_logs WHERE tenant_id IS NULL;
"
psql "$DB" -c "
SELECT 'login_logs NULL count' AS check_name, count(*) AS result
FROM login_logs WHERE tenant_id IS NULL;
"

echo ""
echo "--- 2. Orphan-check (tenant_id verwijst naar niet-bestaande tenant) ---"
psql "$DB" -c "
SELECT 'audit_logs orphans' AS check_name, count(*) AS result
FROM audit_logs al LEFT JOIN tenants t ON t.id = al.tenant_id WHERE t.id IS NULL;
"
psql "$DB" -c "
SELECT 'login_logs orphans' AS check_name, count(*) AS result
FROM login_logs ll LEFT JOIN tenants t ON t.id = ll.tenant_id WHERE t.id IS NULL;
"

echo ""
echo "--- 3. Verdeling per tenant ---"
psql "$DB" -c "SELECT tenant_id, count(*) FROM audit_logs GROUP BY tenant_id ORDER BY 2 DESC;"
psql "$DB" -c "SELECT tenant_id, count(*) FROM login_logs GROUP BY tenant_id ORDER BY 2 DESC;"

echo ""
echo "--- 4. Recente login-records (24h) ---"
psql "$DB" -c "
SELECT ll.tenant_id, ll.user_id, ll.is_success, ll.ip_address, ll.created_at
FROM login_logs ll
WHERE ll.created_at > now() - interval '24 hours'
ORDER BY ll.created_at DESC LIMIT 30;
"

echo ""
echo "=== Klaar. Verwacht: 0 NULL's, 0 orphans ==="
