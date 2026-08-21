# Deploy Report — Search Scalability + Composite Indexes
**Datum:** 21 augustus 2026
**Operator:** Ivan Versteegh (via development@joost.iveras.com)

## Wat werd gedeployd

| Component | Commit | Omschrijving |
|---|---|---|
| Code | `178ed19` | SQL ILIKE search + FTS fix + composite indexes |
| Migratie | `c3d4e5f6a7b8` | Composite indexes: (tenant_id, is_deleted) op subjects/clients/cases, (tenant_id, name) op subjects |

## Rollout procedure

| Stap | Tijd | Status | Bewijs |
|---|---|---|---|
| 1. Backup | 09:55 UTC | ✅ | `/tmp/pre_rollout_20260821.dump` (520KB, pg_dumpFc) |
| 2. RLS tijdelijk uitgeschakeld | 09:54 UTC | ✅ | pg_dump vereiste dit ivm FORCE RLS op 26 tabellen |
| 3. RLS hersteld | 09:55 UTC | ✅ | Alle 26 tabellen: FORCE ROW LEVEL SECURITY |
| 4. Alembic migratie | Eerder vandaag | ✅ | `alembic_version` = `c3d4e5f6a7b8` |
| 5. Health check | 09:55 UTC | ✅ | Zie hieronder |

## Health check resultaat (09:55 UTC)

```json
{
  "status": "ok",
  "database": "connected",
  "db_ping": "ok",
  "migrations": "ok",
  "brave": "ok",
  "hibp": "ok",
  "kadaster": "ok",
  "overheid": "ok",
  "rdw": "ok",
  "spiderfoot": "ok",
  "tor": "ok",
  "disk": {"free_gb": 76.6, "percent_used": 20.1},
  "memory": {"available_gb": 0.4, "percent_used": 80.4}
}
```

## Alembic versie

```
c3d4e5f6a7b8
```

## Git status VPS

```
178ed19 perf(search): replace O(n) decrypt+filter with SQL ILIKE on plaintext name
```

## Rollback-optie

Backup beschikbaar op VPS: `/tmp/pre_rollout_20260821.dump`
Herstel: `pg_restore -d osint_db /tmp/pre_rollout_20260821.dump`

## Opmerkingen

- Backup is naar `/tmp` geschreven (development user had geen schrijfrecht op `/opt/osint-dashboard/backups/`)
- RLS werd tijdelijk uitgeschakeld voor pg_dump, daarna direct hersteld
- Memory usage is hoog (80.4%) — SpiderFoot is de grootverbruiker
