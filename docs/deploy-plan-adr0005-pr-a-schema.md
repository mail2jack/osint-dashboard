# Deployplan — ADR-0005 PR-A (PR #146): `research_actions.investigation_id` schema-invariant

**Status:** DRAFT — documentatie; voert zelf niets uit. Deploy pas na expliciet akkoord; er is **geen** auto-deploy na merge.

**Aanleiding:** ADR-0005 D2 (Optie A). `research_actions` krijgt een nullable `investigation_id` met composite FK `(investigation_id, case_id, tenant_id)` → unieke parent-key `investigations(id, case_id, tenant_id)`. Een gekoppeld onderzoek hoort daarmee gegarandeerd bij dezelfde zaak én tenant. `NULL` = expliciet zaakbreed (bestaande rijen blijven `NULL`, geen backfill). De invariant geldt ook onder RLS-bypass (schema-level; PostgreSQL `23503`).

## 1. Wat de migratie doet (`f5a6b7c8d9e0`, revises `e2f3a4b5c6d7`)

- `investigations`: nieuwe unieke parent-key `uq_investigations_id_case_tenant` op `(id, case_id, tenant_id)`. PG: echte UNIQUE constraint.
- `research_actions`: nieuwe nullable `investigation_id` (String(36)) + index `ix_research_actions_investigation_id` + FK `fk_research_actions_investigation_case_tenant` op `(investigation_id, case_id, tenant_id)` → parent-key.
- **Uitsluitend additieve DDL.** Geen RLS/FORCE-wijziging (apart security-item), geen backfill, geen `sequence_no`/case-number-raakvlak, geen wijziging van bestaande data.
- Down/up baseline: `NULL`-rijen (zaakbreed) blijven ongemoeid; downgrade verwijdert FK + index + kolom en de parent-key.

## 2. Risicobeoordeling

- Laag t.o.v. P1-factuurnummering: additieve migratie zonder seeds/rewrites/RLS. Nieuw risico = enkel FK-afwijzing zodra API-paden (PR-B) `investigation_id` gaat zetten — in deze PR nog niet aanwezig, dus geen gedragsverandering in productie.
- Contract onveranderd op alle bestaande paden; `to_dict()` bevat extra veld → door cliënten te negeren.

## 3. Rollbackbeleid

- Downgrade `f5a6b7c8d9e0` → `e2f3a4b5c6d7` is in-place veilig (geen data-rewrites; kolom weer weggelaten). Over de migratie liggen geen guards; downgrade werkt gewoon.
- Toch: downgrade/restore alleen na expliciete beslissing; preferabel fix-forward of herstel vanaf de stap-5-backup. Geen impliciete `alembic downgrade`.

## 4. Voorbereiding & onderhoudsvenster

- **Merge-freeze op `master` vóór het venster; daarna pas `TARGET_SHA` bepalen.**
  `TARGET_SHA` = `origin/master`-hoofdlijn ná `git fetch`, dynamisch geresolveerd
  tijdens de preflight (niet hardcoden — master kan na de `docs/`-commit alweer
  zijn opgeschoven). Leg de geresolveerde SHA + het fetch-tijdstip vast in het
  rolloutrapport en vereis dat zij gedurende het venster ongewijzigd blijven:
  `git rev-parse origin/master` moét gelijk blijven aan `TARGET_SHA` bij deploy.
- `PREV_SHA` = waarde uit **`$PROJECT_DIR/.deployed_sha`** op de VPS (op dit
  moment `121c104`, na PR #145 — maar always read, niet hardcoden).
  `git merge-base --is-ancestor <PREV_SHA> <TARGET_SHA>` bevestigt dat PR #146
  in de target zit.
- App draaien kan gewoon; de migratie is additief, dus geen schrijfstilstand
  nodig (anders dan de P1-factuurnummers).
- Alembic-head vóór: `e2f3a4b5c6d7`; ná: `f5a6b7c8d9e0` (single head).

## 5. Verse databasebackup + verplichte verificatie (fail-closed, vóór deploy)

1. **Verse archiefbackup** binnen het venster via het bestaande proces
   (`scripts/backup.sh` → `iveras_backup_<ts>.tar.gz.gpg`); label opslaan in
   het rolloutrapport — dit is het externe restore-doel.
2. **`scripts/verify_backup.sh` verplicht** op die archiven (restore naar een
   geïsoleerde PG-database + DR-checks). RC=0 vereist; de verifier heeft zelf
   een freshness-floor (24 u) en weigert stale/slechte archieven.
3. **Fail-closed gate:** als de backup of `verify_backup.sh` niet volledig groen
   is → het venster stopt; **`update.sh` wordt dan niet gestart**. Geen
   "doorskip" of gedeeltelijke uitvoer.
4. `update.sh` maakt daarnaast zelf nóg een pg_dump-kopie
   (`$PROJECT_DIR/backups/<ts>/db.sql`, eerste run) — extra comfort, en telt
   niet als vervanging van stap 1–3.

## 6. Preflight (vóór deploy)

1. App + health up; `/.deployed_sha` == `PREV_SHA`.
2. `git fetch origin` en `TARGET_SHA`-resolve + merge-freeze-bevestiging
   (zie 4); `alembic current` == `e2f3a4b5c6d7` (single head, pre-migratie).
3. Disk/network check; clone op VPS klaar.
4. Verse backup + `verify_backup.sh` groen (stap 5) — **fail-closed gate**.

Droogloop `alembic upgrade head --sql` is niet nodig: de migratie is al bewezen
via de CI roundtrip (SQLite + PostgreSQL).

## 7. Deploy

Bestaande rollout: `sudo ./update.sh` (patroon PR #145). Eerste run: backup + pull `origin/master` (== `TARGET_SHA`) + deps + frontend-build + `alembic upgrade head` (`f5a6b7c8d9e0`) + restart; daarna zelf-herstartrun (skips backup/pull) → `UPDATE_RC=0`, "Update complete".

## 8. Post-deploychecks (in volgorde)

1. `alembic current` == `heads` == `f5a6b7c8d9e0`; `$PROJECT_DIR/.deployed_sha` == `TARGET_SHA` (== HEAD).
2. Healthchecks groen (`/api/v1/health` 200; `systemctl is-active osint-dashboard license-server`).
3. Structuur-verificatie (bypass-sessie):

```sql
-- unieke parent-key + FK aanwezig
SELECT conname, contype FROM pg_constraint
WHERE conname IN ('uq_investigations_id_case_tenant','fk_research_actions_investigation_case_tenant')
ORDER BY conname;
-- FK verwijst naar de parent-key (referentiekolommen case_id+tenant_id mee)
SELECT c.conname, a.attname
FROM pg_constraint c
JOIN unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.conname = 'fk_research_actions_investigation_case_tenant' ORDER BY k.ord;
-- kolom nullable
SELECT column_name, is_nullable FROM information_schema.columns
WHERE table_name='research_actions' AND column_name='investigation_id';
-- index
SELECT indexname FROM pg_indexes WHERE indexname='ix_research_actions_investigation_id';
```

4. Data-intact-verificatie (bypass-sessie) — mag geen mutatie teweegbrengen:

```sql
-- (a) mismatches: gekoppeld onderzoek met andere zaak/tenant dan de actie (moet 0 zijn; bestaande rijen zijn allemaal NULL, dus de check is triviaal)
SELECT count(*) FROM research_actions ra
JOIN investigations iv ON iv.id = ra.investigation_id
WHERE ra.investigation_id IS NOT NULL
  AND (ra.case_id <> iv.case_id OR ra.tenant_id <> iv.tenant_id);
-- (b) NULL-populatie onveranderd (zaakbrede rijen; gelijk aan count(ra) pre- en post-deploy)
SELECT count(*) AS total, count(investigation_id) AS linked FROM research_actions;
-- (c) parent-key inderdaad uniek houdbaar (geen pre-existente pk-duplicaten, moet 1 zijn per id+case+tenant)
SELECT count(*) - count(DISTINCT (id, case_id, tenant_id)) FROM investigations;
```

5. Functioneel (uitsluitend in aangewezen testtenant, na expliciet akkoord): nog geen API-pad zet `investigation_id` in deze PR → geen black-box gedragstest; volstaat met de CI-bewijzen (constraint-suite SQLite, PG `23503`-bypass) + bovenstaande objectchecks.
6. Formeel einde venster; melding + rolloutrapport (SHA's, backup-label, alembic-heads, check-uitvoer) archiveren.

## 9. Uitsluitingen

- Géén deploy vóór akkoord op dit plan; géén uitvoering met oningevulde `PROD_BASE_URL`/`TARGET_SHA`.
- Géén impliciete downgrade; geen FORCE-RLS-extensie in dit venster (los security-item).
- Géén master-mutatie tijdens het venster.