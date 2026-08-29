# Deployplan — P1 invoice-numbering integrity (PR #86)

**Status:** DRAFT — dit plan is pure documentatie en voert zelf **niets** uit op de VPS (de PR is nog niet gemerged; deploy pas na review en expliciet akkoord).
**Aanleiding:** P1-incident 2026-08-29 — case-create in de Neonova-tenant gaf een 500 *nadat* de zaak al gecommit was: de auto-invoice botste op de **globale** unieke index `ix_invoices_invoice_number` (`FAC-2026-00006` bestond al in de Default-tenant). Root cause: allocator `MAX(id)+1`-achtig + RLS-gescopede leesquery tegen een globale unique constraint. Fix in PR #86 (issue #87).

## 1. Wat de migratie doet (`a6b7c8d9e0f1`, revises `dd1e2f3a4b5c7`)

- Nieuwe tellertabel `invoice_number_counters` (PK `(tenant_id, year)`, FK → `tenants.id`, CHECK `next_seq > 0`), op dezelfde atomische `_atomic_next`-manier als `case_number_counters`/`investigation_seq_counters`. **Nooit meer `MAX()+1`.**
- **FORCE RLS** + `tenant_isolation`-policy op `invoice_number_counters` (zelfde patroon als de rest van de RLS-set).
- **Seed** van de tellers uit bestaande `FAC-YYYY-NNNNN`-nummers per `(tenant_id, year)`: `next_seq = hoogst uitgegeven nummer`. Bestaande records worden nooit gewijzigd/hernummerd; niet-standaard nummers worden genegeerd.
- `invoices.invoice_number` verliest de globale unique/index; nieuw `UniqueConstraint ("tenant_id", "invoice_number")` (`uq_tenant_invoice_number`).
- **Contract-verandering:** twee tenants mogen nu bewust hetzelfde `FAC-YYYY-NNNNN` hebben; binnen één tenant blijft hij uniek en sequentieel.

## 2. Begeleidende codewijzigingen

- `allocate_invoice_number()` / `preview_invoice_number()` in `cms/services/sequence_service.py`; handmatige aanmaak (`cms/routes/invoicing.py`) en alle auto-invoice-paden gebruiken de allocator.
- **Case + factuur in één transactie:** case-new, PV-save en action-completion alloceren de factuur vóór de commit. Een invoicefout wordt expliciet afgevangen: `db.session.rollback()`, technische fout server-side gelogd, HTML-gebruiker krijgt een duidelijke flash + redirect (zaak is niet aangemaakt, probeer later opnieuw), JSON-clients een consistente JSON-fout. Geen gedeeltelijke case/client/subject/AuditLog/invoice/counter-write blijft over en er is geen misleidende 500 meer met een al gecommitte zaak.

## 3. Rollbackbeleid (P1)

- **Geen automatische rollback en geen impliciete `alembic downgrade`.** Downgrade alleen na expliciete beslissing (operator + verantwoordelijke) en alleen softwarematig uitvoeren als de downgrade-guard het toelaat.
- **De downgrade is actief beveiligd:** zodra cross-tenant dubbele factuurnummers bestaan (het nieuwe, geldige gedrag) stopt de downgrade van `a6b7c8d9e0f1` **vóór enige DDL** met een duidelijke foutmelding; het terugzetten van de globale unieke index zou namelijk falen.
- **Geldig rollbackpad na uitgifte van cross-tenant dubbele factuurnummers:** *fix-forward*, of herstel vanaf de **pre-deploy backup** (het bestaande `RUNBOOK`-/restore-proces met expliciete SHA en de backup uit stap 5). Geen handicap via downgrade.
- **Als er nog géén cross-tenant duplicaten zijn** (bijv. direct na deploy) is een aftersale-downgrade technisch mogelijk; óók dan alleen na expliciete beslissing, en nooit automatisch.
- Bij een incident: venster gesloten houden; rolloutrapport, baseline, backup-archieflabel, `TARGET_SHA`/`PREV_SHA` en check-uitvoer bewaren; eerst met de verantwoordelijke delen vóór enige actie.

## 4. Voorbereiding & onderhoudsvenster (patroon als het PR-#80/#82-plan)

- Vastleggen `PREV_SHA` en `TARGET_SHA` (actuele `origin/master` ná merge van PR #86; `merge-base --is-ancestor` bewijst dat PR #86 erin zit); merge-freeze op `master` tijdens het hele venster.
- **Schrijfstilstand (P1):** tussen baseline en post-deploychecks mag niemand facturen/zaken aanmaken; anders kan de oude `MAX()+1`-code tijdens/vlak na de migratie alsnog een nummer uitgeven dat de nieuwe allocator ook kan uitgeven. Bevestigen via `pg_stat_activity` zonder actieve write-transacties.
- Alembic-head vóór: `dd1e2f3a4b5c7`; ná: `a6b7c8d9e0f1` (single head).

## 5. Verse databasebackup vóór migratie (verplicht, binnen het venster)

```bash
sudo -u osint /opt/osint-dashboard/scripts/backup.sh /opt/osint-dashboard/backups
ARCHIVE="$(sudo -u osint find /opt/osint-dashboard/backups -maxdepth 1 -type f \
  -name 'iveras_backup_*.tar.gz.gpg' -printf '%T@ %p\n' \
  | sort -n | tail -1 | cut -d' ' -f2-)"
test -n "$ARCHIVE" && test -f "$ARCHIVE" && echo "$ARCHIVE"
```

Archieflabel opslaan in het rolloutrapport — dit is het externe rollbackdoel (`restore.sh --backup <label>`) voor de herstel-van-backup-optie uit stap 3.

## 6. Baseline vóór migratie (inventarisatie, binnen het venster)

Leessessie met `SET app.bypass_rls = 'true';` (anders leeg door FORCE RLS; geen data-verlies):

```sql
-- (A) per (tenant_id, jaar): aantal + hoogste FAC-suffix
WITH canon AS (
  SELECT * FROM (
    SELECT tenant_id,
           CASE WHEN invoice_number ~ '^FAC-[0-9]{4}-[0-9]+$'
                THEN split_part(invoice_number, '-', 2)::int END AS jaar,
           CASE WHEN invoice_number ~ '^FAC-[0-9]{4}-[0-9]+$'
                THEN split_part(invoice_number, '-', 3)::numeric END AS suffix
    FROM invoices WHERE invoice_number IS NOT NULL
  ) genormaliseerd
  WHERE jaar IS NOT NULL
)
SELECT tenant_id, jaar, count(suffix) AS n, coalesce(max(suffix), 0) AS max_canoniek
FROM canon GROUP BY tenant_id, jaar ORDER BY tenant_id, jaar;

-- (B) afwijkende formaten (worden door de seed genegeerd, blijven onveranderd)
SELECT tenant_id, invoice_number FROM invoices
WHERE invoice_number IS NOT NULL
  AND invoice_number !~ '^FAC-[0-9]{4}-[0-9]+$'
ORDER BY tenant_id, invoice_number;

-- (C) al bestaande cross-tenant duplicaten (normaal: 0 vóór deze migratie)
SELECT invoice_number, count(DISTINCT tenant_id) AS tenants
FROM invoices WHERE invoice_number IS NOT NULL
GROUP BY invoice_number HAVING count(DISTINCT tenant_id) > 1;
```

Interpretatie: `(A)` = waarde die `next_seq` moet worden (`= max_canoniek`); `(B)` = afwijkingen, niet meegezaaid; `(C)` moet bij een schone uitrol leeg zijn (de migratie zelf voert géén rename uit).

## 7. Deploy

Bestaande rollout (`production_rollout.sh --confirm DEPLOY-MASTER`, patroon PR-#80/#82-plan): backup → pull `origin/master` (== `TARGET_SHA`, freeze) → deps → frontend-build → `alembic upgrade head` (→ `a6b7c8d9e0f1`) → restart → health → license-server → privacy-purge → rolloutrapport + mail. Vooraf: app **up** zetten (interne doctor), droogloop groen, `pg_stat_activity` leeg.

## 8. Post-deploychecks (in volgorde, binnen het venster)

1. `alembic current` == `a6b7c8d9e0f1`; HEAD/`.deployed_sha` == `TARGET_SHA`.
2. Healthchecks groen (`/api/v1/health`, `systemctl is-active osint-dashboard license-server`).
3. FORCE RLS op `invoice_number_counters` (`relforcerowsecurity` = `true`, policy `tenant_isolation` in `pg_policies`).
4. Baseline `(A)/(B)` opnieuw runnen → identiek aan stap 6 (bestaande nummers ongewijzigd); `(C)` nog steeds leeg in een schone uitrol.
5. Tellers correct gezaaid: `invoice_number_counters.next_seq` == `max_canoniek` per `(tenant, jaar)` uit `(A)`.
6. Functioneel (uitsluitend in aangewezen testtenant, na expliciet akkoord): eerste allocatie = `max_canoniek + 1`; zelfde nummer in twee tienants toestaan, zelfde tenant afwijzen; geforceerde invoicefout bij case-create → duidelijke fout + geen rij (retry maakt exact één zaak+factuur).
7. Formeel einde venster (patroon PR-#80/#82-plan), daarna pas gebruikers informeren + rapportage.

## 9. Uitsluitingen (herhaling)

- Géén deploy/preflight/droogloop vóór akkoord op dit plan; géén uitvoering met oningevulde `PROD_BASE_URL`/`TARGET_SHA`.
- **Géén impliciete `alembic downgrade`** — ooit; de migratie blokkeert de downgrade zelf zodra cross-tenant dubbele factuurnummers bestaan.
- Géén testzaak in productie vóór expliciet akkoord; géén `master`-mutatie tijdens het venster.