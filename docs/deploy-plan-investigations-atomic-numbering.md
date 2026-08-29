# Deployplan — ADR-0002 Onderzoeken + atomic zaaknummering (PR #80)

**Status:** concept — dit plan is pure documentatie en voert zelf **niets** uit op de VPS.
**Aanleiding:** PR #80 introduceert het `investigations`-model plus atomic nummeruitgifte voor zaaknummers (`case_number`) per tenant+jaar en voor onderzoek-sequentienummers per case — nooit meer `MAX()+1`-scans. PR #80 is gemerged op `master` als merge-commit `8b6eb5e178c422871fdf5e0a2d204835dbb06228`; dat is de **laatste bekende includerende commit**, geen vast deploytarget (zie stap 4 en 9).

> **Uitsluitingen (harde randvoorwaarden):**
> - **Geen PR #3.** Het PR3-onderzoeksscherm, de `ResearchAction`-link, backfill en de "Investigation"-rename-sweep maken géén deel uit van deze rollout en mogen hier niet in worden meegenomen.
> - **Geen VPS-actie** (preflight, read-only queries, dry-run of deploy) vóór expliciet akkoord op dit plan.
> - **Geen testzaak in productie** zonder mijn expliciete akkoord. Alle functionele verificaties die data schrijven gebeuren uitsluitend in een aangewezen testtenant en pas na dat akkoord.
> - **Placeholders zijn niet uitvoerbaar**: de operator vervangt vóór elke uitvoering verplicht `PROD_BASE_URL` en legt `TARGET_SHA` vast (zie stap 4). Zonder die invulling is er geen enkele run.

## 1. Doel

Eén gecontroleerde bron voor de VPS-operator om PR #80 uit te rollen: preflight, schrijfstilstand, nulmeting (case_number-inventarisatie), backup, droogloop, deploy-go en post-deploychecks, plus een expliciet rollbackbeleid. Uitvoering gebeurt nooit vanuit dit document deels zelf; de deploy-run is een losse, expliciet geautoriseerde stap met `--confirm`.

## 2. Scope — wat verandert op productie

- **Migratie `bb1c2d3e4f5a7`** (head na deze rollout; revises `aa1b2c3d4e5f6`, getest op SQLite én PostgreSQL):
  1. Nieuwe tabel `investigations` (per case & tenant; composite FK `(case_id, tenant_id)` → `uq_cases_id_tenant`; unique `(tenant_id, case_id, sequence_no)`; CHECK `sequence_no > 0`).
  2. Nieuwe tellertabellen `case_number_counters` (tenant+jaar) en `investigation_seq_counters` (tenant+case) voor atomic issuance; FK `case_number_counters.tenant_id → tenants.id`; CHECK `next_seq > 0` op beide.
  3. **Immutability-triggers**: `cases.case_number` en `investigations.sequence_no` zijn na uitgifte onveranderbaar op DB-niveau (PG: `SQLSTATE 23514`; SQLite: `RAISE(ABORT)`). Zelfs ORM/script/RLS-bypass-paden kunnen ze niet muteren.
  4. **FORCE RLS** + `tenant_isolation`-policy op `investigations`, `case_number_counters`, `investigation_seq_counters` (zelfde patroon als de rest van de RLS-set).
  5. **Seed** `case_number_counters` uit bestaande canonieke zaaknummers (`^[0-9]{4}-[0-9]+$`): `next_seq = hoogst gezaaid nummer`. Bestaande records worden **nooit** gewijzigd of hernummerd; afwijkende formaten worden genegeerd.
- **Routegedrag**: workflow `case_new` allocceert atomic en negeert handmatig invoerveld `raw_number`; de GET-preview gebruikt `peek_case_number` (read-only, nooit allocatie). `case_edit` schrijft `case_number` niet meer.
- **Post-deploy lege tabel**: `investigations` en `investigation_seq_counters` bevatten na migratie 0 rijen (geen backfill in deze rollout).

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
2. **`TARGET_SHA` vastleggen (geen vaste SHA!)**: exact `8b6eb5e` is als target verouderd zodra PR #81 of een andere merge op `master` komt. Capture daarom vlak vóór het onderhoudsvenster de actuele `origin/master` en bewijs dat deze PR #80 bevat:
   ```bash
   sudo -u osint git -C /opt/osint-dashboard fetch --prune origin
   TARGET_SHA="$(sudo -u osint git -C /opt/osint-dashboard rev-parse origin/master)"
   export TARGET_SHA
   sudo -u osint git -C /opt/osint-dashboard merge-base --is-ancestor \
       8b6eb5e178c422871fdf5e0a2d204835dbb06228 "$TARGET_SHA" \
     && echo "OK: TARGET_SHA bevat PR #80: $TARGET_SHA"
   ```
   Vereisten: `TARGET_SHA` bevat PR #80; capturing-, droogloop- en deploy-momenten draaien op de **zelfde** `TARGET_SHA`; daadwerkelijk wegschrijven gebeurt alleen onder merge-freeze.
3. **Alembic-head vóór migratie** (met `DATABASE_URL` uit `.env`):
   ```bash
   sudo -u osint sh -c 'set -a; . /opt/osint-dashboard/.env; set +a; \
     cd /opt/osint-dashboard && venv/bin/alembic current'
   ```
   Verwachting: `aa1b2c3d4e5f6` vóór, `bb1c2d3e4f5a7` ná deploy. **Dit is een preflightwaarde: vlak vóór de echte deploy opnieuw verifiëren.**
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
  SELECT tenant_id,
         CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
              THEN split_part(case_number, '-', 1)::int END   AS jaar,
         CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
              THEN split_part(case_number, '-', 2)::numeric END AS suffix
  FROM cases
  WHERE case_number IS NOT NULL
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

Toelichting: de `CASE WHEN`-gatard — dus per rij — voorkomt dat `split_part(...)::int`/`::numeric` ooit op een afwijkende waarde draait; `::numeric` i.p.v. `::bigint` voorkomt ook overflow op extreem lange numerieke suffixes.

Interpretatie:
- `(A)` geeft per `(tenant_id, jaar)` de waarde die `next_seq` moet worden na deploy (`= max_canoniek`).
- `(B)` is de inventarisatie van afwijkingen. Deze blijven bestaan en worden **niet** meegezaaid; ongedeerd maar wel zichtbaar. Blijkt `(B)` omvangrijker dan voorzien, dan eerst een beslissing over die nummers vóór de deploy.

## 7. Verse databasebackup vóór migratie

Binnen het venster, vóór migratie:

1. Maak de backup en bepaal het **concrete, nieuwe** archiefbestand:
   ```bash
   sudo -u osint /opt/osint-dashboard/scripts/backup.sh /opt/osint-dashboard/backups
   sudo -u osint ls -1t /opt/osint-dashboard/backups/iveras_backup_*.tar.gz.gpg | head -1
   ARCHIVE="/opt/osint-dashboard/backups/iveras_backup_$(date -u +%Y%m%d_%H%M%S).tar.gz.gpg"
   ```
2. **Verifieer alleen na bevestiging dat het aparte DR-account (via `DR_VERIFY_DATABASE_URL` of `PGSERVICE`/`PGHOST`) én de DR-key/config (`DR_BACKUP_KEY_FILE`, standaard `backup-key.gpg` naast het archief) beschikbaar zijn.** `verify_backup.sh` neemt een concreet archiefbestand (géén `--list`):
   ```bash
   sudo -u osint /opt/osint-dashboard/scripts/verify_backup.sh "$ARCHIVE"
   ```
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
   De rollout draait: backup → pull `master` (== `TARGET_SHA`, freeze) → deps → frontend-build → `alembic upgrade head` (→ `bb1c2d3e4f5a7`) → restart → health → license-server → privacy-purge → rolloutrapport + mail.
7. Daarna pas de post-deploychecks (stap 10).

## 10. Post-deploychecks (verplicht, in volgorde; binnen het nog actieve venster)

1. **Alembic-head** = `bb1c2d3e4f5a7`:
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
     SELECT tenant_id,
            CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
                 THEN split_part(case_number, '-', 1)::int END   AS jaar,
            CASE WHEN case_number ~ '^[0-9]{4}-[0-9]+$'
                 THEN split_part(case_number, '-', 2)::numeric END AS suffix
     FROM cases
     WHERE case_number IS NOT NULL
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
   - geen resttoestand achterlaten: testzaak archiveren/verwijderen volgens lokaal protocol.
9. **Formeel einde venster** (stap 5c): service indien nodig starten, health + `alembic current` groen, operator+tijdstip noteren, pas dan gebruikers informeren.
10. **Rolloutrapport** (`rollout-<timestamp>.json` + `checks.tsv`) en deze documentatie samen met baseline + backups bewaren.

## 11. Rollbackbeleid

- **Geen automatische rollback en geen automatische restore.** Download-downgrade of DB-restore alleen na een **expliciete beslissing** van operator + verantwoordelijke; nooit uit een script.
- **Voorkeur fix-forward.** De nieuwe tabellen zijn additief; oudere code kan een lege `investigations`-tabel en de tellers ongezien tolereren. Als iets misgaat: eerst corrigeren, niet terugdraaien.
- **`alembic downgrade` uitsluitend bij expliciete instructie** (en nooit automatisch), en alleen wanneer dataherstel dit werkelijk vereist. De downgrade verwijdert eerst de immutability-triggers, daarna de drie tabellen en `uq_cases_id_tenant` — bestaande zaaknummers blijven onveranderd staan.
- **Rollback-pad (alleen toegestaan)**: het bestaande `RUNBOOK.md`-proces met een **expliciete SHA** en de pre-deploy backup:
  - terug naar `PREV_SHA` via `git checkout <PREV_SHA>` (freeze verhoogd naar alle branches; géén losse experimentele checkouts),
  - `scripts/restore.sh --backup <archieflabel uit stap 7>` (met `restore.sh --list` eerst het archief bevestigen),
  - downgrade van `bb1c2d3e4f5a7` alleen op expliciete instructie, en nooit zonder overleg.
- **Incidentbewijs & deployrapport bewaren**: bij een incident het venster gesloten houden (maintenance); rolloutrapport, deploylog, baseline `(A)/(B)`, backup-archieflabel, `TARGET_SHA`/`PREV_SHA` en alle check-uitvoer bewaren; eerst met de verantwoordelijke delen vóór enige actie.

## 12. Beslispunten / goedkeuringen

- [ ] akkoord op dit rolloutplan (verantwoordelijke)
- [ ] `PROD_BASE_URL` ingevuld door de operator (geen placeholder)
- [ ] `TARGET_SHA` vastgelegd + bevat PR #80 via `merge-base --is-ancestor`
- [ ] groene CI op `TARGET_SHA` (incl. de (deels) nog lopende docs-PR #81 zelf)
- [ ] preflight (stap 4) geslaagd; `PREV_SHA`/`TARGET_SHA`/head/vrije rollbackbeslissing vastgelegd
- [ ] onderhoudsvenster geopend; `osint-dashboard` gestopt; geen actieve write-transacties (stap 5)
- [ ] baseline-inventarisatie (stap 6) binnen het venster opgeslagen
- [ ] verse, geverifieerde backup (stap 7) + label in rapport; DR-account en DR-key beschikbaar bevestigd
- [ ] merge-freeze op `master` actief (stap 9) gedurende het hele venster
- [ ] droogloop bestaande rollout (stap 8) groen op `TARGET_SHA`
- [ ] expliciete `--confirm DEPLOY-MASTER`-go (operator + verantwoordelijke)
- [ ] post-deploychecks (stap 10) allemaal groen; `.deployed_sha`/HEAD == `TARGET_SHA`
- [ ] formeel einde venster genoteerd; gebruikers geïnformeerd (stap 10.9)
- [ ] (alleen mét akkoord) functionele test in aangewezen testtenant + geen resttoestand

## 13. Expliciete uitsluitingen (herhaling)

- Geen deploy, preflight-uitvoering, read-only query of droogloop zónder akkoord op dit plan.
- Geen uitvoering met oningevulde `PROD_BASE_URL` of zonder vastgelegde `TARGET_SHA`.
- Geen PR3-materiaal in deze rollout; geen codewijziging in deze PR.
- Geen testzaak in productie zónder expliciet akkoord.
- Geen rollback/downgrade/restore zónder expliciete beslissing.
- Geen `master`-mutatie tijdens het onderhoudsvenster (merge-freeze).