# Photo Analysis Storage Migration Preflight

Run this read-only query as the deployment role before upgrading to Alembic
head `e0f1a2b3c4d6`:

```sql
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
```

Expected result is zero rows. If rows are returned, stop the deployment and
resolve them manually. Preserve the correct action and archive or otherwise
transition the duplicates according to the incident decision; do not delete or
auto-archive rows as part of the migration. The migration repeats this check
inside its transaction and fails closed with the affected scope counts.

The unique index excludes archived actions with `archived_at IS NULL`. An
archived `pending`/`running` action therefore does not block a new upload, but
restoring it is rejected with HTTP 409 if an active non-archived action now
occupies the same tenant/case/subject scope.
