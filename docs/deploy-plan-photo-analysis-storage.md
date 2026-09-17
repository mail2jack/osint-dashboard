# Photo Analysis Storage Migration Preflight

Run this as the approved read-only production inventory role/configuration used
for prior RLS inventories, before upgrading to Alembic head `e0f1a2b3c4d6`.
Do not accept a result from an ordinary tenant session.

```sql
BEGIN;
SET TRANSACTION READ ONLY;
SET LOCAL app.bypass_rls = 'true';

SELECT current_database() AS database_name,
       current_user AS database_user,
       current_setting('app.bypass_rls', true) AS app_bypass_rls,
       current_setting('row_security', true) AS row_security,
       r.rolbypassrls
FROM pg_roles AS r
WHERE r.rolname = current_user;

DO $$
BEGIN
    IF current_setting('app.bypass_rls', true) <> 'true' THEN
        RAISE EXCEPTION
            'duplicate preflight requires verified app.bypass_rls=true';
    END IF;
END
$$;

SELECT
    tenant_id,
    case_id,
    COALESCE(subject_id, '') AS subject_key,
    COUNT(*) AS active_count,
    ARRAY_AGG(id ORDER BY created_at, id) AS action_ids
FROM research_actions
WHERE action_type = 'photo_analysis'
  AND status IN ('pending', 'running')
  AND archived_at IS NULL
GROUP BY tenant_id, case_id, COALESCE(subject_id, '')
HAVING COUNT(*) > 1
ORDER BY active_count DESC, tenant_id, case_id, subject_key;

ROLLBACK;
```

The identity/context result must show the approved database and `current_user`,
`app_bypass_rls = true`, and the expected RLS capability (`rolbypassrls` or the
approved application bypass configuration). If identity or bypass proof is
missing, stop; a zero-row result is invalid. The duplicate result must also be
zero rows. If rows are returned, stop the deployment and resolve them manually.
Preserve the correct action and archive or otherwise transition the duplicates
according to the incident decision; do not delete or auto-archive rows as part
of the migration. Export only scope IDs and counts needed for that decision,
never product data.

The unique index excludes archived actions with `archived_at IS NULL`. An
archived `pending`/`running` action therefore does not block a new upload, but
restoring it is rejected with HTTP 409 if an active non-archived action now
occupies the same tenant/case/subject scope.

The migration repeats the bypass setup and verification inside its own
transaction before querying duplicates or executing `CREATE UNIQUE INDEX`.
Failure to establish or verify the context fails closed before any index DDL.
