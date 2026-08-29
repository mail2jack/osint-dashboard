# Deployplan — ADR-0002 Onderzoeken + zaaknummering (PR #80 + PR #82)

**Status:** uitgevoerd voor de PR-#82-uitrol van 2026-08-29 (zie §14); dit plan blijft pure documentatie en voert zelf **niets** uit op de VPS. Bevindingen voor volgende runs staan gemarkeerd met `BEVINDING 2026-08-29`.
**Aanleiding:** PR #80 introduceert het `investigations`-model plus atomic nummeruitgifte voor zaaknummers (`case_number`) per tenant+jaar en voor onderzoek-sequentienummers per case — nooit meer `MAX()+1`-scans. PR #82 bouwt daarop het Onderzoeken-scherm in het zaakdetail (create/archive/restore) en maakt `investigations.case_id`/`tenant_id` immuut op DB-niveau. Beide zijn gemerged op `master`: PR #80 als merge-commit `8b6eb5e178c422871fdf5e0a2d204835dbb06228`, PR #82 als merge-commit `21d1da528893d8fd1fcdbba42b07ce5bca01e556` (bevat PR #80). Dat zijn de **laatst bekende includerende commits**, geen vaste deploytargets (zie stap 4 en 9).

> **Uitsluitingen (harde randvoorwaarden):**
> - **Geen ResearchAction-link, backfill of "Investigation"-rename-sweep.** Het Onderzoeken-scherm zelf (PR #82) is wél onderdeel van deze rollout; de eventuele varianten/uitbreidingen (ResearchAction-koppeling, historische backfill, model-renames) vallen erbuiten.
> - **Geen VPS-actie** (preflight, read-only queries, dry-run of deploy) vóór expliciet akkoord op dit plan.
> - **Geen testzaak in productie** zonder mijn expliciete akkoord. Alle functionele verificaties die data schrijven gebeuren uitsluitend in een aangewezen testtenant en pas na dat akkoord.
> - **Placeholders zijn niet uitvoerbaar**: de operator vervangt vóór elke uitvoering verplicht `PROD_BASE_URL` en legt `TARGET_SHA` vast (zie stap 4). Zonder die invulling is er geen enkele run.

## 1. Doel

Eén gecontroleerde bron voor de VPS-operator om **PR #80 + PR #82** uit te rollen: preflight, schrijfstilstand, nulmeting (case_number-inventarisatie), backup, droogloop, deploy-go en post-deploychecks, plus een expliciet rollbackbeleid. Uitvoering gebeurt nooit vanuit dit document deels zelf; de deploy-run is een losse, expliciet geautoriseerde stap met `--confirm`.

## 2. Scope — wat verandert op productie

- **Migratie `bb1c2d3e4f5a7`** (PR #80; revises `aa1b2c3d4e5f6`, getest op SQLite én PostgreSQL; in deze rollout sluit hij een combinéerde uitrol af als de PR #80-uitrol nog niet live is — head ná de rollout is altijd `dd1e2f3a4b5c7`):
  1. Nieuwe tabel `investigations` (per case & tenant; composite FK `(case_id, tenant_id)` → `uq_cases_id_tenant`; unique `(tenant_id, case_id, sequence_no)`; CHECK `sequence_no > 0`).
  2. Nieuwe tellertabellen `case_number_counters` (tenant+jaar) en `investigation_seq_counters` (tenant+case) voor atomic issuance; FK `case_number_counters.tenant_id → tenants.id`; CHECK `next_seq > 0` op beide.
  3. **Immutability-triggers**: `cases.case_number` en `investigations.sequence_no` zijn na uitgifte onveranderbaar op DB-niveau (PG: `SQLSTATE 23514`; SQLite: `RAISE(ABORT)`). Zelfs ORM/script/RLS-bypass-paden kunnen ze niet muteren.
  4. **FORCE RLS** + `tenant_isolation`-policy op `investigations`, `case_number_counters`, `investigation_seq_counters` (zelfde patroon als de rest van de RLS-set).
  5. **Seed** `case_number_counters` uit bestaande canonieke zaaknummers (`^[0-9]{4}-[0-9]+$`): `next_seq = hoogst gezaaid nummer`. Bestaande records worden **nooit** gewijzigd of hernummerd; afwijkende formaten worden genegeerd.
- **Routegedrag**: workflow `case_new` allocceert atomic en negeert handmatig invoerveld `raw_number`; de GET-preview gebruikt `peek_case_number` (read-only, nooit allocatie). `case_edit` schrijft `case_number` niet meer.
- **PR #82 — migratie `dd1e2f3a4b5c7`** (revises `bb1c2d3e4f5a7`): DB-triggers (PG én SQLite) die UPDATEs op `investigations.case_id`/`tenant_id` na uitgifte blokkeren (PG `SQLSTATE 23514`; SQLite `RAISE(ABORT)`). De `sequence_no`-immutability uit `bb1c2d3e4f5a7` blijft behouden.
- **PR #82 — routes**: het Onderzoeken-scherm op zaakdetail (create/archive/restore); create gebruikt uitsluitend de centrale nummeruitgifte (`sequence_service`), nooit handmatig `sequence_no`; archive/restore zetten naast `archived_at` ook `status` (`archived`/`open`); dubbele state-overgang → `409` (JSON) zonder extra AuditLog; viewer krijgt alleen-lezen bij case-toegang.
- **Post-deploy lege tabel**: `investigations` en `investigation_seq_counters` bevatten na de migratie 0 rijen (geen backfill in deze rollout); eventuele rijen ontstaan pas door de functionele tests in de aangewezen testtenant (stap 10.8).

## 3. Risicobeoordeling

- **Nummervrijgave verandert van algoritme** (scan naar counter): risico is alleen een *botsing met de eerste uitgifte*. De seed voorkomt dat via `next_seq = max(canoniek)`; niet-canonieke nummers kunnen nooit botsen met uitgifte-formaat `{jaar}-{5-cijferig}`.
- **Schrijfstilstand is vereist (P1)**: zonder read-only venster kan de **oude** app tijdens/vlak na de counter-seed nog een zaak aanmaken met de oude `MAX()+1`-logica en kan de nieuwe allocator dat nummer opnieuw uitgeven; ook wordt de vóór/na-baseline onbetrouwbaar. Zie stap 5.
- **Immutability-triggers** blokkeren nu ook "onschuldige" correcties van `case_number`. Oude UI-paden die nummers herschreven (workflow edit) zijn in deze PR al aangepast; de legacy-CRUD staat het niet toe. Geen backdoor waargenomen.
- Non-destructief: er wordt geen bestaand zaakrecord aangepast, geen data herschreven, geen bestaand nummer gewijzigd.
- Grootste operationele risico's: (a) een niet-gesynchroniseerde/ghedivergeerde VPS-`master`, (b) een `master`-mutatie **tijdens** het venster — beide worden door `TARGET_SHA` + merge-freeze geëlimineerd (stap 4 en 9).

## 4. Productiepreflight (read-only; vóór alle andere stappen)

Alle commando's op de VPS als root. Leg de uitvoer **samen met het rolloutrapport** vast.

0. **Verplichte voorbereiding (geen placeholder oningevuld)**: vervang `PROD_BASE_URL` door de echte productie-host. Zolang dat niet is ingevuld is géén van de commando's hieronder uit te voeren:
   ```bash
   PROD_BASE_URL="<VUL HIER DE ECHTE PRODUCTIE-HOST IN, bv. https://dashboard.iveras.com>"
   ```
1. **Huidige applicatie-SHA** (= `PREV_SHA`, de vorige commit voor de rollout):
   ```bash
   PREV_SHA="$(sudo -u osint git -C /opt/osint-dashboard rev-parse HEAD)"
   echo "$PREV_SHA"; cat /opt/osint-dashboard/.deployed_sha
   ```
2. **`TARGET_SHA` vastleggen (geen vaste SHA!)**: exact `8b6eb5e` of exact `21d1da5` is als target verouderd zodra een andere merge op `master` komt. Capture daarom vlak vóór het onderhoudsvenster de actuele `origin/master` en bewijs dat deze **zowel PR #80 als PR #82** bevat:
   ```bash
   sudo -u osint git -C /opt/osint-dashboard fetch --prune origin
   TARGET_SHA="$(sudo -u osint git -C /opt/osint-dashboard rev-parse origin/master)"
   export TARGET_SHA
   sudo -u osint git -C /opt/osint-dashboard merge-base --is-ancestor \
       8b6eb5e178c422871fdf5e0a2d204835dbb06228 "$TARGET_SHA" \
     && echo "OK: TARGET_SHA bevat PR #80: $TARGET_SHA"
   sudo -u osint git -C /opt/osint-dashboard merge-base --is-ancestor \
       21d1da528893d8fd1fcdbba42b07ce5bca01e556 "$TARGET_SHA" \
     && echo "OK: TARGET_SHA bevat PR #82: $TARGET_SHA"
   ```
   Vereisten: `TARGET_SHA` bevat PR #80 én PR #82; capturing-, droogloop- en deploy-momenten draaien op de **zelfde** `TARGET_SHA`; daadwerkelijk wegschrijven gebeurt alleen onder merge-freeze.
3. **Alembic-head vóór migratie** (met `DATABASE_URL` uit `.env`):
   ```bash
   sudo -u osint sh -c 'set -a; . /opt/osint-dashboard/.env; set +a; \
     cd /opt/osint-dashboard && venv/bin/alembic current'
   ```
   Verwachting: `bb1c2d3e4f5a7` vóór (als de PR #80-uitrol al live is), `dd1e2f3a4b5c7` ná deploy. **Melden de preflight een andere vóór-stand (bijv. `aa1b2c3d4e5f6` omdat PR #80 nog niet live is), dan stopt de operator hier: het betreft een combinéerde rollout van PR #80 + PR #82 en de operator stemt eerst de scope af.** De vóór-waarde wordt vlak vóór de echte deploy opnieuw geverifieerd; ná-waarde is altijd `dd1e2f3a4b5c7`.
4. **Services & healthchecks**:
   ```bash
   systemctl is-active osint-dashboard license-server spiderfoot
   curl -fsS http://127.0.0.1:5000/health
   curl -fsS http://127.0.0.1:8000/health
   curl -fsS "$PROD_BASE_URL/api/v1/health"
   ```
   Verwachting: alles `active`; status `{"status":"ok"}`.
5. **Vrije rollbackbeslissing (vastleggen)**: vóór de deploy expliciet noteren dat er **geen automatische rollback** is, en dat er geen dwang is om te rollbackben: de beslissing is vrij bij de operator + verantwoordelijke, à la het bestaande plan. Opname in het rolloutrapport (`rollout-report.json`).

## 5. Onderhoudsvenster & schrijfstilstand vóór migratie (P1)

Doel: tussen baseline en (post-deploy-)checks mag **niemand** zaken aanmaken of werkers laten schrijven. Zonder dit venster kan de oude `MAX()+1`-code tijdens/vlak na de seed een dubbele uitgifte veroorzaken en is de vóór/na-vergelijking niets waard.

**Stap a — nieuwe case-writes en werkers stoppen/blokkeren (reversibel):**
```bash
sudo systemctl stop osint-dashboard      # UI/API + in-process async-actie-werkers uit
```
Asynchrone actie-uitvoering draait als in-process threads van `osint-dashboard`; `stop` blokkeert dus zaakcreatie én werkers samen. `license-server` (eigen `license.db`) en `spiderfoot` (eigen scanner-DB, niet de app-PostgreSQL) schrijven niet op de app-DB en mogen blijven draaien — de rollout-controle checkt deze diensten wel op `active`.

> **BEVINDING 2026-08-29:** een volledig gestopte app vóór `--confirm DEPLOY-MASTER` doet de interne preflight van `production_rollout.sh` afbreken — doctor checkt `curl localhost:5000/health?quick=1` en faalt dan (rollout stopt vóór enige mutatie, rc=1, rollbackrapport geschreven). De feitelijke schrijfstilstand zit **in** `update.sh` (stop → pull/deps/build → `alembic upgrade` → start). Werkwijze tijdens de uitrol: baseline + venster mét gestopte app; vlak vóór `--confirm` `osint-dashboard` herstarten; de effectieve write-freeze = stop-rond-migratie binnen de deploy + de `pg_stat_activity`-controle uit stap b.

**Stap b — bevestigen dat er geen actieve write-transacties meer bestaan:**
```sql
SELECT pid, state, now() - xact_start AS xact_leeftijd,
       left(query, 120) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
ORDER BY state, xact_leeftijd DESC;
```
Verwachting: **geen rijen** (RAMEN zonder actieve sessie). Rijen in `active` of `idle in transaction` → wachten/laten uitdoven; pas verder gaan zodra de query leeg is. Deze bevestiging wordt opnieuw uitgevoerd vlak vóór `--confirm`.

**Stap c — gecontroleerd hervatten:** herstart gebeurt als onderdeel van de deploy zelf (`update.sh` 6/7 restartt `osint-dashboard`); dit is het gecontroleerde herstartpunt. Het **formele einde van het venster** wordt pas genoteerd (operator + tijdstip) nadat alle post-deploychecks groen zijn:
```bash
sudo systemctl start osint-dashboard            # alleen nodig als nog gestopt
curl -fsS "$PROD_BASE_URL/api/v1/health"
sudo -u osint sh -c 'set -a; . /opt/osint-dashboard/.env; set +a; \
  cd /opt/osint-dashboard && venv/bin/alembic current'
```
Tot dat formele einde meldt de operator géén hervatting aan gebruikers.

**Gegevens-betrouwbaarheid**: de baseline (stap 6) wordt **binnen** het venster opgenomen (app gestopt), en de schrijfgevoelige post-checks (stap 10) worden vóór het formele einde uitgevoerd. Elke zaak die na de migratie door de **nieuwe** allocator aangemaakt is, telt mee als legitieme `next_seq`-verhoging; de operator verantwoordt per nummer (AuditLog `create`-entries binnen het venster).

## 6. Nulmeting — inventarisatie case_number per tenant/jaar (+ afwijkende formaten)

Productie draait met FORCE RLS; open **lees-sessies** daarom met `SET app.bypass_rls = 'true';` (anders leeg resultaat — geen data-verlies). Verplichte **baseline vóór deploy**, binnen het venster opgenomen (bewaar uitvoer als artefact; diff ná deploy exact identiek):

```sql
SET app.bypass_rls = 'true';

-- Normalisatie naar veilige CTE: ALLEEN canonieke rijen krijgen jaar/suffix;
-- afwijkende waarden leveren NULL op en worden nooit ge-cast (P1).
WITH canon AS (
  SELECT * FROM (
    SELECT tenant_id,
           CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
                THEN split_part(case_number, '-', 1)::int END   AS jaar,
           CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
                THEN split_part(case_number, '-', 2)::numeric END AS suffix
    FROM cases
    WHERE case_number IS NOT NULL
  ) genormaliseerd
  -- afwijkende nummers hebben jaar IS NULL en horen niet in (A) als NULL-groep
  WHERE jaar IS NOT NULL
)
-- (A) canonieke nummers per tenant/jaar: aantal + hoogste suffix
SELECT tenant_id,
       jaar,
       count(suffix)               AS n_canoniek,
       coalesce(max(suffix), 0)    AS max_canoniek
FROM canon
GROUP BY tenant_id, jaar
ORDER BY tenant_id, jaar;

-- (B) afwijkende formaten (worden door de seed genegeerd en blijven onveranderd)
SELECT tenant_id, case_number
FROM cases
WHERE case_number IS NOT NULL
  AND case_number !~ '^[0-9]{4}-[0-9]+$'
ORDER BY tenant_id, case_number;
```

Toelichting: de `CASE WHEN`-guard — dus per rij — voorkomt dat `split_part(...)::int`/`::numeric` ooit op een afwijkende waarde draait; het expliciete `WHERE jaar IS NOT NULL` filtert niet-canonieke rijen uit de CTE, zodat ze niet als een extra NULL-groep in `(A)` verschijnen; `::numeric` i.p.v. `::bigint` voorkomt ook overflow op extreem lange numerieke suffixes.

Interpretatie:
- `(A)` geeft per `(tenant_id, jaar)` de waarde die `next_seq` moet worden na deploy (`= max_canoniek`).
- `(B)` is de inventarisatie van afwijkingen. Deze blijven bestaan en worden **niet** meegezaaid; ongedeerd maar wel zichtbaar. Blijkt `(B)` omvangrijker dan voorzien, dan eerst een beslissing over die nummers vóór de deploy.

## 7. Verse databasebackup vóór migratie

Binnen het venster, vóór migratie:

1. Maak de backup en leg daarna het **concreet aangemaakte** archiefbestand vast op basis van bestandsselectie (niet uit de kloktijd afgeleid), en valideer dat het bestaat vóórdat er iets geverifieerd wordt:
   ```bash
   sudo -u osint /opt/osint-dashboard/scripts/backup.sh /opt/osint-dashboard/backups
   ARCHIVE="$(sudo -u osint find /opt/osint-dashboard/backups -maxdepth 1 -type f \
     -name 'iveras_backup_*.tar.gz.gpg' -printf '%T@ %p\n' \
     | sort -n | tail -1 | cut -d' ' -f2-)"
   test -n "$ARCHIVE" && test -f "$ARCHIVE"
   echo "$ARCHIVE"
   ```
2. **Verifieer alleen na bevestiging dat het aparte DR-account (via `DR_VERIFY_DATABASE_URL` of `PGSERVICE`/`PGHOST`) én de DR-key/config (`DR_BACKUP_KEY_FILE`, standaard `backup-key.gpg` naast het archief) beschikbaar zijn.** `verify_backup.sh` neemt het concreet gekozen, bestaande archiefbestand (géén `--list`):
   ```bash
   sudo -u osint /opt/osint-dashboard/scripts/verify_backup.sh "$ARCHIVE"
   ```
   > **BEVINDING 2026-08-29:** zolang `DR_VERIFY_DATABASE_URL`/`PGSERVICE`/`PGHOST` niet geconfigureerd zijn faalt `verify_backup.sh` **alleen** op de `database_restore`-gate (alle andere checks passen). Toegestane, toen expliciet afgesproken vervangende verificatie: lokale scratch-restore in een wegwerp-DB op dezelfde host — decrypt met `gpg -q --batch --yes --pinentry-mode loopback --passphrase-file <naastliggende backup-key.gpg> -d "$ARCHIVE"`, `createdb osint_verify_<ts>`, `gunzip database.sql.gz | psql -d osint_verify_<ts>`, controle `alembic_version` = verwachte vóór-stand + counts + FORCE RLS, dan `dropdb`. De dump restoret triggers/policies mee; superuser (postgres) omzeilt FORCE RLS tijdens het herstel.
3. Archieven alleen **op-sommen** met `restore.sh --list` (read-only; draait nooit een restore):
   ```bash
   sudo -u osint /opt/osint-dashboard/scripts/restore.sh --list
   ```
4. Archieflabel(s) van de verse, geverifieerde backup **opslaan in het rolloutrapport** — dit is het externe rollbackdoel voor een eventuele restore.

## 8. Droogloop van de bestaande productierollout

Eén lees-sessie die niets wijzigt, en die de bestaande rollout-flow (preflight, deploy-plan, readiness) volledig valideert, op **dezelfde `TARGET_SHA`**:

```bash
# voorwaarde aan het begin: VPS-master stond op TARGET_SHA (freeze actief) — dan:
sudo /opt/osint-dashboard/scripts/production_rollout.sh --dry-run
sudo -u osint git -C /opt/osint-dashboard rev-parse HEAD        # moet == TARGET_SHA zijn
```

- Controleert o.a. `branch=master`, aanwezigheid van deploy/update/backup/license-scripts, en draait `deploy.sh --dry-run` (= `preflight.sh` + geplande update-volgorde).
- Uitvoer van alle checks bewaren. **Geen vervolgstap** zonder groene droogloop en zonder akkoord. Als `origin/master` na capturing is bewogen, dan is de droogloop ongeldig: her-capture `TARGET_SHA` (opnieuw met de PR-#80-ancestor-check) en draai de droogloop opnieuw.

## 9. Gecontroleerde deploy — pas na expliciet `--confirm`-akkoord

Opdelen in expliciete acties; geen automatische vervolgactie:

1. **Akkoord op dit plan** (verantwoordelijke).
2. **Merge-freeze op `master` is verplicht (P1)**: `production_rollout.sh` → `deploy.sh` → `update.sh` doet `git pull origin master` (update.sh stap 2/7) en heeft dus géén vaste commit. Freeze daarom `master` van het vastleggen van `TARGET_SHA` t/m het formele einde van het venster: **geen enkele merge/push naar `master`**. (Alternatief bestaand pad: `deploy.sh <TARGET_SHA>` pint via `DEPLOY_PIN` en skipt de pull — maar dat omzeilt de rollout-wrapper en diens rapportage/mail en wordt daarom niet gebruikt zonder expliciete afspraak.)
3. **Re-verificatie vlak vóór de echte deploy** (preflightwaarden opnieuw controleren; alles moet onveranderd zijn t.o.v. de droogloop):
   ```bash
   sudo -u osint git -C /opt/osint-dashboard fetch --prune origin
   sudo -u osint git -C /opt/osint-dashboard rev-parse origin/master   # == TARGET_SHA
   sudo -u osint git -C /opt/osint-dashboard rev-parse HEAD            # == TARGET_SHA
   # alembic current (zie stap 4.3) opnieuw draaien
   ```
4. **Schrijfstilstand bevestigen** (stap 5b opnieuw, vlak vóór `--confirm`): `pg_stat_activity` zonder actieve write-transacties.
5. **Groene droogloop** (stap 8) + nogmaals preflight (stap 4).
6. **Expliciete deploy-go door operator én verantwoordelijke**, mét het mantra-akkoord `--confirm DEPLOY-MASTER`:
   ```bash
   sudo /opt/osint-dashboard/scripts/production_rollout.sh --confirm DEPLOY-MASTER
   ```
   De rollout draait: backup → pull `master` (== `TARGET_SHA`, freeze) → deps → frontend-build → `alembic upgrade head` (→ `dd1e2f3a4b5c7`) → restart → health → license-server → privacy-purge → rolloutrapport + mail.

   > **BEVINDING 2026-08-29:** vóór dit commando moet `osint-dashboard` **up** staan (interne preflight/doctor — zie stap 5a; de eerste run brak daarop af vóór enige mutatie, tweede run na herstart was rc=0). De merge-freeze/vensterlogica blijft ongewijzigd geldig: géén merges naar `master`, `TARGET_SHA` ongewijzigd.

7. Daarna pas de post-deploychecks (stap 10).

## 10. Post-deploychecks (verplicht, in volgorde; binnen het nog actieve venster)

1. **Alembic-head** = `dd1e2f3a4b5c7`:
   ```bash
   sudo -u osint sh -c 'set -a; . /opt/osint-dashboard/.env; set +a; \
     cd /opt/osint-dashboard && venv/bin/alembic current'
   ```
2. **Gedeployde commit == `TARGET_SHA`**: `.deployed_sha` én `HEAD`:
   ```bash
   cat /opt/osint-dashboard/.deployed_sha            # == TARGET_SHA
   sudo -u osint git -C /opt/osint-dashboard rev-parse HEAD
   ```
3. **Healthchecks groen**: `$PROD_BASE_URL/api/v1/health` = `{"status":"ok"}`; `systemctl is-active osint-dashboard license-server`; `curl -fsS http://127.0.0.1:5000/health`.
4b. **Route-existence Onderzoeken** (PR #82; routes leven onder `/cms/workflow`, **niét** onder `/api/v1`; ongeauthet is `302`→login resp. `400` het bewijs dat de route leeft, `404` betekent afwezig):
   ```bash
   CID="$(su - postgres -c "psql -At -d osint_db -c 'SELECT id FROM cases LIMIT 1'")"
   curl -s -o /dev/null -w '%{http_code}\n' "$PROD_BASE_URL/cms/workflow/case/$CID/investigations"                                # 302
   curl -s -o /dev/null -w '%{http_code}\n' -X POST --max-time 20 "$PROD_BASE_URL/cms/workflow/api/investigations/00000000-0000-0000-0000-000000000001/archive"   # 400
   curl -s -o /dev/null -w '%{http_code}\n' -X POST --max-time 20 "$PROD_BASE_URL/cms/workflow/api/case/$CID/investigations"                                       # 400
   ```
4. **RLS / FORCE RLS actief** op de drie nieuwe tabellen:
   ```sql
   SET app.bypass_rls = 'true';
   SELECT relname, relforcerowsecurity
   FROM pg_class
   WHERE relname IN ('investigations', 'case_number_counters', 'investigation_seq_counters')
   ORDER BY 1;
   ```
   Verwachting: `relforcerowsecurity` = `true` voor alle drie; per tabel een policy `tenant_isolation` in `pg_policies`.
5. **Bestaande zaaknummers ongewijzigd**: re-run van baseline `(A)` en `(B)` uit stap 6 → exact identieke output (diff bewaren naast de baseline).
6. **Tellers correct gezaaid** uit de canonieke nummers (zelfde veilige CTE als in stap 6 → nooit casts op afwijkende waarden):
   ```sql
   SET app.bypass_rls = 'true';
   WITH canon AS (
     SELECT * FROM (
       SELECT tenant_id,
              CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
                   THEN split_part(case_number, '-', 1)::int END   AS jaar,
              CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
                   THEN split_part(case_number, '-', 2)::numeric END AS suffix
       FROM cases
       WHERE case_number IS NOT NULL
     ) genormaliseerd
     WHERE jaar IS NOT NULL
   )
   SELECT c.tenant_id, c.year, c.next_seq,
          coalesce(max(canon.suffix), 0)                          AS max_canoniek,
          (c.next_seq::numeric = coalesce(max(canon.suffix), 0))  AS klopt
   FROM case_number_counters c
   LEFT JOIN canon
     ON canon.tenant_id = c.tenant_id
    AND canon.jaar     = c.year
   GROUP BY c.tenant_id, c.year, c.next_seq
   ORDER BY 1, 2;
   ```
   Verwachting: `klopt` = `true` voor alle rijen; geen tellerrij voor een `(tenant, jaar)` zonder canonieke zaken. Eerste nieuwe allocatie = `max_canoniek + 1`. Een afwijking is alleen acceptabel als de operator **elke** sinds-deploy aangemaakte zaak (via `case_number` in het venster, AuditLog `create`) kan verantwoorden als legitieme `next_seq`-verhoging. `investigations` en `investigation_seq_counters` bevatten 0 rijen.
7. **Case create-preview schrijft niets**: snapshot vóór/na openen van de GET-preview (`/cms/workflow/case_new`):
   ```sql
   SET app.bypass_rls = 'true';
   SELECT count(*), count(updated_at) FROM case_number_counters;
   SELECT count(*) FROM investigations;
   ```
   Beide query's vóór en ná een preview-oproep → identieke waarden (geen rij gecreëerd, geen `next_seq` gewijzigd, geen `investigations`-rij).
7b. **PR #82 immutability-triggers aanwezig** (DB heet `osint_db`; vastleggen uit `pg_trigger`; op èchte rijen pas in 8 testen):
   ```sql
   SET app.bypass_rls = 'true';
   SELECT r.relname, t.tgname, t.tgenabled::text
   FROM pg_trigger t JOIN pg_class r ON r.oid = t.tgrelid
   WHERE NOT t.tgisinternal AND t.tgname LIKE 'trg_%'
   ORDER BY r.relname, t.tgname;
   ```
   Verwachting: `investigations :: trg_investigations_identity_immutable` (case_id/tenant_id, uit `dd1e2f3a4b5c7`) naast `investigations :: trg_investigations_sequence_no_immutable` en `cases :: trg_cases_case_number_immutable` (uit `bb1c2d3e4f5a7`); `tgenabled` = `O` (origin) voor alle drie.
8. **Functional (alleen in de aangewezen testtenant, en alleen na expliciet akkoord)**:
   - noteren `next_seq` voor `(testtenant, huidig jaar)`;
   - nieuwe zaak aanmaken via `/cms/workflow/case_new` → nummer = opvolgend (`next_seq + 1`), uniek onder `uq_tenant_case_number`;
   - **wijziging `case_number` geweigerd**: via UI (`case_edit`) en als backstop via SQL:
     ```sql
     SET app.bypass_rls = 'true';
     UPDATE cases SET case_number = '9999-00001' WHERE id = :nieuwe_zaak;
     -- verwachting: SQLSTATE 23514 'case_number is immutable after issuance (ADR-0002 D4)'
     ```
     Rij blijft ongewijzigd (OOK zonder `WHERE`-restrictie verwacht de trigger dit te blokkeren).
   - **PR #82 — onderzoeken-scherm**:
     - op het zaakdetail (nieuwe zaak van hierboven) opent de Onderzoeken-sectie en toont geen rijen;
     - create via `/cms/workflow/case/<id>/investigations` → `human_number` = `<case_number>-<seq:02d>`, uniek per case; create wijzigt `sequence_no`/`case_id`/`tenant_id` niet;
     - archive → `status`=`archived` + `archived_at` gezet; dubbel archive → `409` én **geen extra** AuditLog-rij; restore → `status`=`open` + `archived_at`=NULL; dubbel restore → `409` én geen extra AuditLog-rij;
- immutability-backstop op een zojuist gecreëerde `investigations`-rij. **Elke verwachte falende `UPDATE` draait in een eigen psql-sessie/transactie**: na de eerste verwachte triggerfout is de transactie in PostgreSQL `aborted` en zou een volgende opdracht `25P02` geven — nooit case_id/tenant_id/sequence_no achter elkaar in één sessie zetten, en elke sessie na de falende `UPDATE` expliciet afsluiten met `ROLLBACK`:
        - per poging: nieuwe sessie openen (`SET app.bypass_rls = 'true';` + `BEGIN;`), **één** falende `UPDATE`, gerapporteerd **SQLSTATE = `23514`** controleren (bij voorkeur `\errverbose` voor de ERRCONSTRAINT/triggernaam), dan `ROLLBACK;` (geen verdere statements in die sessie);
        - na iedere poging **opnieuw verbinden/herladen** en per `SELECT` verifiëren dat de rij nog de oorspronkelijke `case_id`, `tenant_id` en `sequence_no` heeft;
        - `1a) case_id` — verplaatsing naar een **tweede, geldige testzaak in dezelfde testtenant** (bewijst dat de identity-trigger de verplaatsing blokkeert, niet dat een ongeldige FK faalt):
          ```sql
          SET app.bypass_rls = 'true';
          BEGIN;
          UPDATE investigations SET case_id = :tweede_testzaak_id (zelfde testtenant)
            WHERE id = :inv;
          -- verwachting: SQLSTATE 23514 'investigation.case_id is immutable ...'
          ROLLBACK;
          ```
        - `1b) tenant_id` — hertoewijzing naar een andere tenant-id (deze UPDATE sneuvelt óók op de composite FK, maar de BEFORE-trigger vuurt eerst; SQLSTATE moet dus van de trigger komen):
          ```sql
          SET app.bypass_rls = 'true';
          BEGIN;
          UPDATE investigations SET tenant_id = :andere_tenant_id WHERE id = :inv;
          -- verwachting: SQLSTATE 23514 'investigation.tenant_id is immutable ...'
          ROLLBACK;
          ```
        - `1c) sequence_no` — (bescherming uit `bb1c2d3e4f5a7`):
          ```sql
          SET app.bypass_rls = 'true';
          BEGIN;
          UPDATE investigations SET sequence_no = 999 WHERE id = :inv;
          -- verwachting: SQLSTATE 23514 'investigation.sequence_no is immutable ...'
          ROLLBACK;
          ```
          Afwijkend SQLSTATE (bijv. `25P02`, `23503` op `1a`) → de trigger werkt niet zoals bedoeld; stoppen en eerst onderzoeken.
   - viewer-alleen-lezen: een viewer met case-toegang ziet de sectie, kan niets aanmaken/archiveren;
   - geen resttoestand achterlaten: testzaak + testonderzoeken archiveren/verwijderen volgens lokaal protocol.
9. **Formeel einde venster** (stap 5c): service indien nodig starten, health + `alembic current` groen, operator+tijdstip noteren, pas dan gebruikers informeren.
10. **Rolloutrapport** (`rollout-<timestamp>.json` + `checks.tsv`) en deze documentatie samen met baseline + backups bewaren.

## 11. Rollbackbeleid

- **Geen automatische rollback en geen automatische restore.** Download-downgrade of DB-restore alleen na een **expliciete beslissing** van operator + verantwoordelijke; nooit uit een script.
- **Voorkeur fix-forward.** De nieuwe tabellen zijn additief; oudere code kan een lege `investigations`-tabel en de tellers ongezien tolereren. Als iets misgaat: eerst corrigeren, niet terugdraaien.
- **`alembic downgrade` uitsluitend bij expliciete instructie** (en nooit automatisch), en alleen wanneer dataherstel dit werkelijk vereist. De downgrade van `dd1e2f3a4b5c7` verwijdert eerst de PR-#82 identity-triggers (case_id/tenant_id); de downgrade van `bb1c2d3e4f5a7` verwijdert daarna de `sequence_no`-/`case_number`-triggers en de drie tabellen + `uq_cases_id_tenant` — bestaande zaaknummers blijven onveranderd staan.
- **Rollback-pad (alleen toegestaan)**: het bestaande `RUNBOOK.md`-proces met een **expliciete SHA** en de pre-deploy backup:
  - terug naar `PREV_SHA` via `git checkout <PREV_SHA>` (freeze verhoogd naar alle branches; géén losse experimentele checkouts),
  - `scripts/restore.sh --backup <archieflabel uit stap 7>` (met `restore.sh --list` eerst het archief bevestigen),
  - downgrade van `dd1e2f3a4b5c7` (en zo nodig `bb1c2d3e4f5a7`) alleen op expliciete instructie, en nooit zonder overleg.
- **Incidentbewijs & deployrapport bewaren**: bij een incident het venster gesloten houden (maintenance); rolloutrapport, deploylog, baseline `(A)/(B)`, backup-archieflabel, `TARGET_SHA`/`PREV_SHA` en alle check-uitvoer bewaren; eerst met de verantwoordelijke delen vóór enige actie.

## 12. Beslispunten / goedkeuringen

- [ ] akkoord op dit rolloutplan (verantwoordelijke)
- [ ] `PROD_BASE_URL` ingevuld door de operator (geen placeholder)
- [ ] `TARGET_SHA` vastgelegd + bevat PR #80 (`8b6eb5e`) én PR #82 (`21d1da5`) via `merge-base --is-ancestor`
- [ ] groene CI op `TARGET_SHA`
- [ ] preflight (stap 4) geslaagd, **inclusief bevestigde alembic-vóórstand** (`bb1c2d3e4f5a7` óf combinéerde rollout-afspraak); `PREV_SHA`/`TARGET_SHA`/head/vrije rollbackbeslissing vastgelegd
- [ ] onderhoudsvenster geopend; `osint-dashboard` gestopt; geen actieve write-transacties (stap 5)
- [ ] baseline-inventarisatie (stap 6) binnen het venster opgeslagen
- [ ] verse, geverifieerde backup (stap 7) + label in rapport; DR-account en DR-key beschikbaar bevestigd
- [ ] merge-freeze op `master` actief (stap 9) gedurende het hele venster
- [ ] droogloop bestaande rollout (stap 8) groen op `TARGET_SHA`
- [ ] expliciete `--confirm DEPLOY-MASTER`-go (operator + verantwoordelijke)
- [ ] post-deploychecks (stap 10) allemaal groen; `.deployed_sha`/HEAD == `TARGET_SHA`
- [ ] formeel einde venster genoteerd; gebruikers geïnformeerd (stap 10.9)
- [ ] (alleen mét akkoord) functionele test in aangewezen testtenant **inclusief het Onderzoeken-scherm (create/archive/restore, dubbele overgang → 409, immutability-backstop)** + geen resttoestand

## 13. Expliciete uitsluitingen (herhaling)

- Geen deploy, preflight-uitvoering, read-only query of droogloop zónder akkoord op dit plan.
- Geen uitvoering met oningevulde `PROD_BASE_URL` of zonder vastgelegde `TARGET_SHA`.
- Geen PR3-materiaal **buiten het Onderzoeken-scherm** in deze rollout (geen ResearchAction-link, backfill of rename-sweep); geen codewijziging in deze docs-PR.
- Geen testzaak in productie zónder expliciet akkoord.
- Geen rollback/downgrade/restore zónder expliciete beslissing.
- Geen `master`-mutatie tijdens het onderhoudsvenster (merge-freeze).

## 14. Uitgevoerde uitrol — 2026-08-29 (PR #82; PR #80 was al live)

Uitgevoerd met expliciet akkoord (`--confirm DEPLOY-MASTER`). Bewijsmateriaal op de VPS onder `/opt/osint-dashboard/reports/` en `backups/`:

- **SHA's**: `PREV_SHA`/`.deployed_sha` vóór = `00d71aaec82c9d149fdc210bb41cb7238d63d03b`; `TARGET_SHA` (origin/master tijdens merge-freeze) = `1b7b1d7ab2f6e8405dbb0c221f0ddcb046be0b7e`, bewezen ancestorschap van PR #80 (`8b6eb5e`) én PR #82 (`21d1da5`).
- **Alembic**: vóór `bb1c2d3e4f5a7` → ná `dd1e2f3a4b5c7` (head).
- **Baseline** `(A)/(B)`: één canonieke tenant `3a169c92-04a2-48f9-be1b-1fcf930c0f0f` (Default Organization): 2023=25/96, 2024=25/100, 2025=22/99, 2026=31/100; `(B)` = geen afwijkende formaten. Artefact: `reports/rollout/baseline-pr82-20260829T174119Z.txt`.
- **Backup + DR**: `backups/iveras_backup_20260829_174126.tar.gz.gpg`; `verify_backup.sh` faalde alleen op `database_restore` (DR-account niet geconfigureerd) → opgevangen via de in §7 toegestane lokale scratch-restore: `createdb osint_verify_20260829T174341Z` → `gunzip database.sql.gz | psql` → `alembic_version` = `bb1c2d3e4f5a7`, counts cases=103 / subjects=354 / investigations=0 / investigation_seq_counters=0 / case_number_counters=4, FORCE RLS `true` → `dropdb`. Restore-pad bevestigd.
- **Deploy**: eerste run rc=1 (geaborteerd door de doctor-check "server gestopt", zie stap 5a); na herstart van `osint-dashboard` rc=0; `update.sh VOLTOOID (1b7b1d7)`; rapport `reports/rollout/rollout-20260829T174632Z.json`.
- **Post-deploychecks (10.1–10.5 + 7b) groen**: HEAD/`.deployed_sha` = `1b7b1d7`; triggers `trg_investigations_identity_immutable` + `trg_investigations_sequence_no_immutable` (investigations) en `trg_cases_case_number_immutable` (cases), allemaal `O`; FORCE RLS `true` op `investigations`, `case_number_counters`, `investigation_seq_counters`; `case_number_counters.next_seq` = 96/100/99/100 (onveranderd t.o.v. de baseline); cases 25/25/22/31 = baseline; extern health ok; Onderzoeken-routes levend (screen `302`, create/archive/restore `400`, niét `404`); geen drift zónder nieuwe zaken in het venster.
- **Nog open — stap 10.8** (functionele checks): uitsluitend mét expliciet akkoord en **in de aangewezen testtenant** (beschikbare actieve tenants: Neonova Nederland `00d72d7c-869c-43dd-886e-71282abb5351`, Acme Corp Rotterdam `1d428db5-e5fc-41a9-9ed3-2c12f14b4978`): create/archive/restore incl. dubbele overgang → `409` zonder extra AuditLog; immutability-backstop per **losse sessie** (één falende `UPDATE`, SQLSTATE `23514` controleren, `ROLLBACK`, opnieuw verbinden/herladen + rijcontrole; `1a) case_id` naar tweede geldige testzaak in dezelfde testtenant, `1b) tenant_id` naar andere tenant, `1c) sequence_no`) — daarna formeel venstereinde (10.9) + rapportage (10.10).
- **Structuur-observaties voor volgende runs**: app-PostgreSQL heet `osint_db` (niét `osint`); `case_number_counters` = `(tenant_id, year, next_seq, updated_at)`; `cases` heeft géén jaar-kolom (jaar = prefix uit `case_number`, exact zoals de §6-CTE); `tenants` kent `slug`/`name` (géén `code`); trigger-enum casten als `tgenabled::text`.
- **Open vervolgactie (na de rollout)**: `DR_VERIFY_DATABASE_URL`/`PGSERVICE`/`PGHOST` structureel invullen zodat `verify_backup.sh` blijvend groen is zonder scratch-restore.