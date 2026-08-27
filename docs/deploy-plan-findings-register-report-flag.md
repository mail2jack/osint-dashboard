# Deployplan — Findings Register + include-in-report vlag

**Status:** concept — deze PR is alleen documentatie en voert zelf geen deploy uit.
**Aanleiding:** via PR #65 (gemerged als `885c9ba`) bevat `master` het centrale findings-register, de rapportselectievlag `findings.include_in_report` (optie b ADR-0001) en Fase D-deep-links op legacy-schermen.

## 1. Doel

Eén gecontroleerde bron voor de VPS-operator om deze feature uit te rollen: nulmetingen, verificatie en rollback. De uitrol zelf gebeurt nooit vanuit deze PR; dat is een losse, expliciet geautoriseerde deploy-run na akkoord.

## 2. Scope — wat verandert op productie

- **Migratie** `a2b3c4d5e6f7`: zuivere nullable boolean `findings.include_in_report`; geen data-her-schrijf; idempotente guard (`_has_column`); getest op SQLite en PostgreSQL.
- **Semantiek**: `NULL`/`true` → opgenomen in officiële rapporten, `false` → uitgesloten. Backward compatibel: bestaande bevindingen zijn ongewijzigd en blijven opgenomen.
- **Fase D (UX)**: case-detail, subject-view en subject-profile tonen deep-links naar het register i.p.v. inline-findingslijsten; zoekresultaten wijzen naar `workflow.case_detail`.
- **Nieuwe routes**: `/cms/workflow/findings`, `/cms/workflow/api/findings` en het report-flag-endpoint — alle alleen voor `investigator`+.

## 3. Risicobeoordeling

- Non-destructief en backward-compatible. Officiële rapporten veranderen inhoudelijk niet zolang niets wordt geëxcludeerd (NULL → opgenomen).
- Grootste gedragswijziging is navigatie/UI (register i.p.v. inline-lijsten) plus de vlag-functionaliteit zelf.

## 4. Nulmetingen / baseline vóór de deploy (bewijslast)

1. **Leg de actuele status vast** — niet hardcoded, want sinds eerdere migraties kan de head veranderen:
   - applicatie-commit (`git rev-parse HEAD`, ook terug te vinden via `.deployed_sha`),
   - **actuele Alembic-head** via `alembic current` (met `DATABASE_URL` uit `.env`).
2. **Backup**: `update.sh` maakt automatisch een pre-deploy backup (stap 1/7). Optioneel extra zekerheid: handmatig `scripts/backup.sh`.
3. **Per pilotzaak**, vóór de deploy:
   - aantal **actieve, niet-verwijderde** findings (register-semantiek: `is_deleted = false`, niet gearchiveerd), gemeten met dezelfde gebruiker en filtercontext als de verificatie ná de deploy;
   - een **gegenereerd officieel rapport/PV** (HTML/PDF) als bewijsexport, dat na deploy opnieuw wordt gegenereerd voor de diff.
4. Bewaar deze baseline samen met het rolloutrapport.

## 5. Volgorde uitrol (verplicht)

1. **Dry-run** (geen wijzigingen):
   ```
   sudo /opt/osint-dashboard/scripts/production_rollout.sh --dry-run
   ```
2. **Expliciet akkoord** van de verantwoordelijke — geen automatische vervolgactie.
3. **Deploy**:
   ```
   sudo /opt/osint-dashboard/scripts/production_rollout.sh --confirm DEPLOY-MASTER
   ```
   Dit draait: backup → pull → deps → frontend-build → `alembic upgrade head` → restart → health → license-server → privacy-purge → rapport + mail.
4. **Functionele pilot** (stap 6) → **rapport-onveranderdheid** (stap 7) → pas daarna breder gebruik.

## 6. Verificatie na deploy

- `alembic current` controleert dat de DB op de head van de gedeployde code staat (idempotent opnieuw draaien levert geen wijzigingen).
- `/api/v1/health` bereikbaar; `osint-dashboard` en `license-server` actief.
- Register: `/cms/workflow/findings` geeft 200 voor een investigator; nav-item 📋 Findings aanwezig.
- **Register-count is contextafhankelijk**: afhankelijk van de ingelogde gebruiker (case-access via `get_accessible_case_ids`), van archiefstatus (`show_archived`) en van actieve filters (case, subject, status, report, zoekterm). Vergelijk daarom **per pilotzaak** met dezelfde gebruiker + dezelfde filtercontext als de baseline — niet één globaal getal.
- Vlag-test op een test-/kladzaak (niet op echte data): exclude → opnieuw gegenereerd rapport/PV mist de bevinding; re-include → terug.
- Legacy-schermen: deep-links + counts werken; zoekresultaten openen correct.

## 7. Rapport-onveranderdheid

- Genereer ná de deploy voor elke pilotzaak hetzelfde officiële rapport/PV opnieuw en diff deze tegen de baseline-export.
- Een verschil moet altijd verklaarbaar zijn (bijv. een bewuste vlag-actie) of is een bug. De deploy zelf mag rapporten inhoudelijk niet veranderen.

## 8. Rollback

Er is **geen automatische rollback**. En bovendien:

- **Geen losse `git checkout` als standaardprocedure.** Het enige pad is het bestaande, gedocumenteerde rollback-/restorepad in `RUNBOOK.md`, met een **expliciete SHA** en de pre-deploy backup (uit `update.sh` stap 1/7 of handmatig `scripts/backup.sh`).
- **Voorkeur: fix-forward.** De nieuwe nullable kolom blijft in de DB — oudere code tolereert een extra kolom probleemloos. Migratie-downgrade alleen wanneer dataherstel dit expliciet vereist (bijv. een volledige restore), altijd na expliciete instructie en nooit automatisch.
- Bij incident: maintenance-mode behouden; rolloutrapport, deploylog en baseline bewaren; fout delen met de verantwoordelijke vóór enige actie.

## 9. Pilot & breder gebruik

- Pilot op gekozen tenant/case(s); operators en onderzoekers krijgen een korte instructie: bevindingen beheer je centraal in het register; "excluderen uit PV" is een bewuste vlag.
- Beslissing wat in officiële rapporten mag: blijft bij de onderzoeksleider/verantwoordelijke; elke exclusie is auditbaar via het AuditLog (`Workflow set include_in_report=…`).
- Na geslaagde pilot + rapport-onveranderdheid → breder gebruik. Daarna een afzonderlijke DR-rehearsal volgens het rollback-runbook.
- **Geen concreet pilotmoment in dit document**; dat wordt vastgesteld ná deze docs-PR en de definitieve deploy-go.

## 10. Beslispunten / goedkeuringen

- [ ] akkoord op dit plan
- [ ] groene CI op de te deployen commit
- [ ] dry-run geslaagd op de VPS
- [ ] expliciete deploy-go (operator + verantwoordelijke)
- [ ] pilotzaak(-en) geselecteerd
- [ ] baseline + bewijsexports opgeslagen
- [ ] na pilot: review rapport-onveranderdheid → breder gebruik