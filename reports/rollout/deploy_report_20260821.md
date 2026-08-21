# Deploy Report — Search Scalability + Composite Indexes + Data Recovery
**Datum:** 21 augustus 2026
**Operator:** Ivan Versteegh (via development@joost.iveras.com + osint@joost.iveras.com)

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

## Data Recovery (10:24 UTC)

Bij pilot-inventarisatie bleek dat **alle subjects en cases weg waren** (0 rijen).
De case_subjects junction data (22 rijen) verwees naar niet-bestaande entities.

### Oorzaak
Waarschijnlijk verloren gegaan tijdens het CASCADE TRUNCATE incident op 20 augustus 2026
of de daaropvolgende hersteloperaties. De `manual_20260819.dump` en `post_recovery_20260820.dump`
bevatten alleen junction data, geen subjects/cases.

### Herstelprocedure
1. Gedecrypteerde backup `iveras_backup_20260819_120001.tar.gz.gpg` (3.5MB)
2. `database.sql.gz` bevatte volledige SQL dump met subjects + cases
3. COPY FROM faalde door RLS → INSERT statements met ON CONFLICT DO NOTHING gegenereerd
4. RLS tijdelijk uitgeschakeld (`ALTER TABLE ... DISABLE ROW LEVEL SECURITY`)
5. Data ingevoerd + RLS weer ingeschakeld
6. Alembic migratie opnieuw uitgevoerd (full restore had version gereset)

### Resultaat
| Entiteit | Aantal |
|---|---|
| Subjects (actief) | 26 |
| Cases (actief) | 8 |
| Case_subjects | 22 |
| Subject_relations | 2 |
| Research_actions | 80 |
| Alembic version | `c3d4e5f6a7b8` ✅ |
| Health status | `ok` ✅ |

### LESSON LEARNED
- Full SQL restore herstelt ook het schema + policies → Alembic versie wordt gereset
- `COPY FROM` blokkeert op tabellen met RLS policies (zelfs met NO FORCE)
- `INSERT ... ON CONFLICT DO NOTHING` toont "INSERT 0 0" maar voert wél uit (misleidend)
- CASCADE TRUNCATE op `cases` verwijdert ook `research_actions` → gebruik TRUNCATE zonder CASCADE of TRUNCATE specifieke tabellen
