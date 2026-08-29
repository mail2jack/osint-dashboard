# Deployplan — ADR-0002 Onderzoeken + atomic zaaknummering (PR #80)

**Status:** concept — dit plan is pure documentatie en voert zelf **niets** uit op de VPS.
**Aanleiding:** PR #80 (gemerged op `master` als merge-commit `8b6eb5e178c422871fdf5e0a2d204835dbb06228`) introduceert het `investigations`-model plus atomic nummeruitgifte voor zaaknummers (`case_number`) per tenant+jaar en voor onderzoek-sequentienummers per case — nooit meer `MAX()+1`-scans.

> **Uitsluitingen (harde randvoorwaarden):**
> - **Geen PR #3.** Het PR3-onderzoeksscherm, de `ResearchAction`-link, backfill en de "Investigation"-rename-sweep maken géén deel uit van deze rollout en mogen hier niet in worden meegenomen.
> - **Geen VPS-actie** (preflight, read-only queries, dry-run of deploy) vóór expliciet akkoord op dit plan.
> - **Geen testzaak in productie** zonder mijn expliciete akkoord. Alle functionele verificaties die data schrijven gebeuren uitsluitend in een aangewezen testtenant en pas na dat akkoord.

## 1. Doel

Eén gecontroleerde bron voor de VPS-operator om PR #80 uit te rollen: preflight, nulmeting (case_number-inventarisatie), backup, droogloop, deploy-go en post-deploychecks, plus een expliciet rollbackbeleid. Uitvoering gebeurt nooit vanuit dit document deels zelf; de deploy-run is een losse, expliciet geautoriseerde stap met `--confirm`.

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
- **Immutability-triggers** blokkeren nu ook "onschuldige" correcties van `case_number`. Oude UI-paden die nummers herschreven (workflow edit) zijn in deze PR al aangepast; de legacy-CRUD staat het niet toe. Geen backdoor waargenomen.
- Non-destructief: er wordt geen bestaand zaakrecord aangepast, geen data herschreven, geen bestaand nummer gewijzigd.
- Grootste operationele risico: een niet-gesyncroniseerde/ghedivergeerde VPS-`master` → detecteerbaar en oplosbaar vóór deploy in de preflight/droogloop.

## 4. Productiepreflight (read-only; vóór alle andere stappen)

Alle commando's op de VPS als root. Leg de uitvoer **samen met het rolloutrapport** vast.

1. **Huidige applicatie-SHA** (= vorige commit voor de rollout):
   ```bash
   sudo -u osint git -C /opt/osint-dashboard rev-parse HEAD
   cat /opt/osint-dashboard/.deployed_sha
   ```
2. **Alembic-head vóór migratie** (met `DATABASE_URL` uit `.env`):
   ```bash
   sudo -u osint sh -c 'set -a; . /opt/osint-dashboard/.env; set +a; \
     cd /opt/osint-dashboard && venv/bin/alembic current'
   ```
   Verwachting: `aa1b2c3d4e5f6` vóór, `bb1c2d3e4f5a7` ná deploy.
3. **Services & healthchecks**:
   ```bash
   systemctl is-active osint-dashboard license-server spiderfoot
   curl -fsS http://127.0.0.1:5000/health
   curl -fsS http://127.0.0.1:8000/health
   curl -fsS https://<productie-host>/api/v1/health
   ```
   Verwachting: alles `active`; status `{"status":"ok"}`.
4. **Vrije rollbackbeslissing (vastleggen)**: vóór de deploy expliciet noteren dat er **geen automatische rollback** is, en dat er geen dwang is om te rollbackben: de beslissing is vrij bij de operator + verantwoordelijke, à la het bestaande plan. Opname in het rolloutrapport (`rollout-report.json`).

## 5. Nulmeting — inventarisatie case_number per tenant/jaar (+ afwijkende formaten)

Productie draait met FORCE RLS; open **lees-sessies** daarom met `SET app.bypass_rls = 'true';` (anders leeg resultaat — geen data-verlies).

Verplichte **baseline vóór deploy** (bewaar uitvoer als artefact; diff-tijd na deploy exact identiek):

```sql
SET app.bypass_rls = 'true';

-- (A) canonieke nummers per tenant/jaar: aantal + hoogst bekende suffix
SELECT tenant_id,
       split_part(case_number, '-', 1)::int                    AS jaar,
       count(*)                                                AS n_canoniek,
       max(split_part(case_number, '-', 2)::bigint)            AS max_canoniek
FROM cases
WHERE case_number ~ '^[0-9]{4}-[0-9]+$'
GROUP BY tenant_id, jaar
ORDER BY tenant_id, jaar;

-- (B) afwijkende formaten (worden door de seed genegeerd en blijven onveranderd)
SELECT tenant_id, case_number
FROM cases
WHERE case_number IS NOT NULL
  AND case_number !~ '^[0-9]{4}-[0-9]+$'
ORDER BY tenant_id, case_number;
```

Interpretatie:
- `(A)` geeft per `(tenant_id, jaar)` de waarde die `next_seq` moet worden na deploy (`= max_canoniek`).
- `(B)` is de inventarisatie van afwijkingen. Deze blijven bestaan en worden **niet** meegezaaid; ongedeerd maar wel zichtbaar. Blijkt `(B)` groter dan menen we, dan eerst een beslissing over die nummers vóór de deploy.

## 6. Verse databasebackup vóór migratie

1. `update.sh` maakt automatisch een pre-deploy backup (stap 1/7) **en** er komt een extra verse, geverifieerde backup in deze rollout:
   ```bash
   sudo -u osint /opt/osint-dashboard/scripts/backup.sh /opt/osint-dashboard/backups
   sudo -u osint /opt/osint-dashboard/scripts/verify_backup.sh --list
   ```
2. Archieflabel(s) van de verse backup (bv. `iveras_backup_YYYYMMDD_HHMMSS.tar.gz.gpg`) **opslaan in het rolloutrapport** — dit is het externe rollbackdoel voor een eventuele restore.

## 7. Droogloop van de bestaande productierollout

Eén lees-sessie die niets wijzigt, en die de bestaande rollout-flow (preflight, deploy-plan, readiness) volledig valideert:

```bash
sudo /opt/osint-dashboard/scripts/production_rollout.sh --dry-run
```

- Controleert o.a. `branch=master`, aanwezigheid van deploy/update/backup/license-scripts, en draait `deploy.sh --dry-run` (= `preflight.sh` + geplande update-volgorde).
- Uitvoer van alle checks bewaren. **Geen vervolgstap** zonder groene droogloop en zonder akkoord.

## 8. Gecontroleerde deploy — pas na expliciet `--confirm`-akkoord

Opdelen in expliciete acties; geen automatische vervolgactie:

1. **Akkoord op dit plan** (verantwoordelijke).
2. **Sync-check**: VPS-`master` zit op de merge-commit van PR #80 (`8b6eb5e…`, = `origin/master`), niet gedivergeerd:
   ```bash
   sudo -u osint git -C /opt/osint-dashboard rev-parse HEAD
   sudo -u osint git -C /opt/osint-dashboard fetch --prune origin
   sudo -u osint git -C /opt/osint-dashboard rev-parse origin/master
   ```
3. **Groene droogloop** (stap 7) + nogmaals preflight (stap 4).
4. **Expliciete deploy-go door operator én verantwoordelijke**, mét het mantra-akkoord `--confirm DEPLOY-MASTER`:
   ```bash
   sudo /opt/osint-dashboard/scripts/production_rollout.sh --confirm DEPLOY-MASTER
   ```
   De rollout draait: backup → pull `master` (bevat `8b6eb5e`) → deps → frontend-build → `alembic upgrade head` (→ `bb1c2d3e4f5a7`) → restart → health → license-server → privacy-purge → rolloutrapport + mail.
5. Daarna pas de post-deploychecks (stap 9).

## 9. Post-deploychecks (verplicht, in volgorde)

1. **Alembic-head** = `bb1c2d3e4f5a7`:
   ```bash
   sudo -u osint sh -c 'set -a; . /opt/osint-dashboard/.env; set +a; \
     cd /opt/osint-dashboard && venv/bin/alembic current'
   ```
2. **Healthchecks groen**: `/api/v1/health` = `{"status":"ok"}`; `systemctl is-active osint-dashboard license-server`; `curl -fsS http://127.0.0.1:5000/health`.
3. **RLS / FORCE RLS actief** op de drie nieuwe tabellen:
   ```sql
   SET app.bypass_rls = 'true';
   SELECT relname, relforcerowsecurity
   FROM pg_class
   WHERE relname IN ('investigations', 'case_number_counters', 'investigation_seq_counters')
   ORDER BY 1;
   ```
   Verwachting: `relforcerowsecurity` = `true` voor alle drie; per tabel een policy `tenant_isolation` in `pg_policies`.
4. **Bestaande zaaknummers ongewijzigd**: re-run van baseline `(A)` en `(B)` uit stap 5 → exact identieke output (diff bewaren naast de baseline).
5. **Tellers correct gezaaid** uit de canonieke nummers:
   ```sql
   SET app.bypass_rls = 'true';
   SELECT c.tenant_id, c.year, c.next_seq,
          coalesce(MAX(split_part(cs.case_number, '-', 2)::bigint), 0) AS max_canoniek,
          (c.next_seq = coalesce(MAX(split_part(cs.case_number, '-', 2)::bigint), 0)) AS klopt
   FROM case_number_counters c
   LEFT JOIN cases cs
     ON cs.tenant_id = c.tenant_id
    AND split_part(cs.case_number, '-', 1)::int = c.year
    AND cs.case_number ~ '^[0-9]{4}-[0-9]+$'
   GROUP BY c.tenant_id, c.year, c.next_seq
   ORDER BY 1, 2;
   ```
   Verwachting: `klopt` = `true` voor alle rijen; en geen tellerrij voor een `(tenant, jaar)` zonder canonieke zaken. Eerste nieuwe allocatie = `max_canoniek + 1`. `investigations` en `investigation_seq_counters` bevatten 0 rijen.
6. **Case create-preview schrijft niets**: snapshot vóór/na openen van de GET-preview (`/cms/workflow/case_new`):
   ```sql
   SET app.bypass_rls = 'true';
   SELECT count(*), count(updated_at) FROM case_number_counters;
   SELECT count(*) FROM investigations;
   ```
   Beide query's vóór en ná een preview-oproep → identieke waarden (geen rij gecreëerd, geen `next_seq` gewijzigd, geen `investigations`-rij).
7. **Functional (alleen in de aangewezen testtenant, en alleen na expliciet akkoord)**:
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
8. **Rolloutrapport** (`rollout-<timestamp>.json` + `checks.tsv`) en deze documentatie samen met baseline + backups bewaren.

## 10. Rollbackbeleid

- **Geen automatische rollback en geen automatische restore.** Download-downgrade of DB-restore alleen na een **expliciete beslissing** van operator + verantwoordelijke; nooit uit een script.
- **Voorkeur fix-forward.** De nieuwe tabellen zijn additief; oudere code kan een lege `investigations`-tabel en de tellers ongezien tolereren. Als iets misgaat: eerst corrigeren, niet terugdraaien.
- **`alembic downgrade` uitsluitend bij expliciete instructie** (en nooit automatisch), en alleen wanneer dataherstel dit werkelijk vereist. De downgrade verwijdert eerst de immutability-triggers, daarna de drie tabellen en `uq_cases_id_tenant` — bestaande zaaknummers blijven onveranderd staan.
- **Rollback-pad**: enige toegestane route is het bestaande `RUNBOOK.md`-proces (`git checkout <vorige SHA>` + `scripts/restore.sh` met de backup uit stap 6), met expliciete SHA en het backuparchieflabel.
- **Incidentbewijs & deployrapport bewaren**: bij een incident maintenance-mode houden; rolloutrapport, deploylog, baseline `(A)/(B)`, backup-archieflabel en alle check-uitvoer bewaren; het beste eerst met de verantwoordelijke delen vóór enige actie.

## 11. Beslispunten / goedkeuringen

- [ ] akkoord op dit rolloutplan (verantwoordelijke)
- [ ] groene CI op de te deployen commit (`8b6eb5e…`) — reeds success op `master`
- [ ] preflight (stap 4) geslaagd; SHA/head/services vastgelegd
- [ ] baseline-inventarisatie (stap 5) opgeslagen
- [ ] verse, geverifieerde backup (stap 6) gemeld + label in rapport
- [ ] droogloop bestaande rollout (stap 7) groen
- [ ] VPS-`master` op `8b6eb5e…`, niet gedivergeerd
- [ ] expliciete `--confirm DEPLOY-MASTER`-go (operator + verantwoordelijke)
- [ ] post-deploychecks (stap 9) allemaal groen
- [ ] (alleen mét akkoord) functionele test in aangewezen testtenant + geen resttoestand

## 12. Expliciete uitsluitingen (herhaling)

- Geen deploy, preflight-uitvoering, read-only query of droogloop zónder akkoord op dit plan.
- Geen PR3-materiaal in deze rollout.
- Geen testzaak in productie zónder expliciet akkoord.
- Geen rollback/downgrade/restore zónder expliciete beslissing.