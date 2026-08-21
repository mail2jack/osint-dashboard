# ADR-0001 Subject Profile Pilot Report
**Datum:** 21 augustus 2026
**Status:** Pilot geslaagd
**Uitvoerder:** Ivan Versteegh

---

## Uitvoeringsbewijs

| Onderdeel | Waarde |
|-----------|--------|
| Gedeployde Git SHA | `e113cee533e3859b3ca295cf28cc9bf1b3caf5bc` |
| Alembic head | `c3d4e5f6a7b8` |
| Backup (pre-rollout) | `/tmp/pre_rollout_20260821.dump` (520KB, pg_dumpFc) op VPS |
| Backup (automatisch) | `/opt/osint-dashboard/backups/iveras_backup_20260821_060001.tar.gz.gpg` |
| Deploy rapport | `reports/rollout/deploy_report_20260821.md` |
| Tijdstip pilot | 21 augustus 2026, ~10:00-11:00 UTC+2 |
| VPS | `joost.iveras.com` (osint@joost.iveras.com) |
| Productie URL | `https://joost.iveras.com` |

---

## Samenvatting

De Subject Profile pilot is opgestart op productie. Alle functies werken correct per tenant.
Geen decryptiefouten, geen performance-problemen, geen veiligheidsissues geconstateerd.

---

## Per-Tenant Resultaten

### Default Organization (`3a169c92`)
| Onderdeel | Status | Details |
|-----------|--------|---------|
| Subjects | 26 | 15 person, 5 company, 3 online, 2 vehicle, 1 property |
| Cases | 8 | Toyota, Openbaar, Rotterdam, Verlofhouders, DK, Old Friends, Sollicitatie, Pilot acceptatietest |
| Case-Subject links | 22 | |
| Research Actions | 80 | |
| Search | OK | Herman, Marloes, Romeo gevonden via SQL ILIKE |
| Subject Profile | OK | Actie-kaart aanwezig, Merge-kaart aanwezig |
| Case Detail | OK | Subject names zichtbaar, geen ciphertext |
| Ciphertext | 0 | Alle encrypted fields correct ontsleuteld |

### Neonova Nederland (`00d72d7c`)
| Onderdeel | Status | Details |
|-----------|--------|---------|
| Feature Flag | Aan | Per-tenant ingeschakeld |
| Subjects | 0 | Geen data (verwacht) |
| Cases | 0 | Geen data (verwacht) |

### Acme Corp Rotterdam (`1d428db5`)
| Onderdeel | Status | Details |
|-----------|--------|---------|
| Feature Flag | Uit | Niet ingeschakeld |
| Subjects | 0 | Geen data (verwacht) |
| Cases | 0 | Geen data (verwacht) |

---

## Globale Feature Flags
- `subject_first_investigations_global`: **Aan** (kill-switch)
- Per-tenant: Default Organization + Neonova Nederland aan, Acme Corp uit

---

## Browser Smoke Tests

9/9 geslaagd op productie (`https://joost.iveras.com`):

| Test | Status |
|------|--------|
| Login + 2FA | Pass |
| Case detail: geen ciphertext | Pass |
| Case detail: bekende subject names zichtbaar | Pass |
| Subject search: Marloes | Pass |
| Subject search: Herman | Pass |
| Subject profile: laadt | Pass |
| Subject profile: geen ciphertext | Pass |
| Subject profile: actie-kaart | Pass |
| Subject profile: merge-kaart | Pass |

---

## Security Review Status

| Item | Status |
|------|--------|
| TOTP secret geroteerd | Ja — 2FA reset uitgevoerd, nieuw secret geconfigureerd via authenticator app |
| Wachtwoord geroteerd | Ja — productiewachtwoord gewijzigd na incident (plaintext in rapport) |
| Sessies ongeldig verklaard | Wachtwoordrotatie volstaat voor pilot-schaal (1 gebruiker) |
| Geleide rollout uitgevoerd | Ja — backup, health check, deploy report |
| CI regressiesuite | 736/736 + 9/9 browser smoke tests |
| Browser smoke tests: staging-first policy | Documentie aanwezig |
| Secrets in git history | Geen productiewachtwoorden of TOTP secrets gevonden |

---

## Security Incident: Plaintext Wachtwoord in Rapport

**Tijdens het opstellen van dit rapport is een productiewachtwoord in plaintext opgenomen.**
Het betreffende bestand (`pilot_report_20260821.md`) was untracked en nooit gecommit of gepusht.

**Genomen maatregelen:**
1. Productiewachtwoord onmiddellijk geroteerd naar nieuw random wachtwoord
2. Gecontroleerd of het wachtwoord ooit gecommit is — **niet het geval** (bestand was untracked)
3. Git history gescand op alle bekende wachtwoorden — **geen productie-credentials gevonden**
4. Dit rapport herschreven zonder plaintext wachtwoorden
5. Lesson learned: credentials alleen in password manager/secret store, nooit in documentatie

---

## Issues Tijdens Pilot

1. **TOTP secret niet gepersisteerd** — De "rotatie" van het TOTP secret was uitgevoerd in een sessie maar niet gecommit naar de database. De authenticator app had het nieuwe secret, de DB het oude. Opgelost door 2FA reset via Flask shell.

2. **Wachtwoord onbekend** — Testwachtwoorden uit `conftest.py` werkten niet voor het productieaccount. Wachtwoord gereset via VPS Flask shell.

3. **case_id fixture bug** — De `case_id` test fixture maakte een eigen niet-geauthenticeerde sessie aan in plaats van de authenticated `session` fixture te gebruiken. Opgelost in commit `e113cee`.

---

## Aanbevelingen

1. **Default Organization** is klaar voor gebruik — alle functies werken, data is intact.
2. **Neonova Nederland** kan data importeren zodra gebruikers actief worden.
3. **Acme Corp Rotterdam** kan worden ingeschakeld via feature flag wanneer nodig.
4. **Monitoring:** Sentry staat actief, geen fouten in de laatste hour.
5. **Smoke test account:** Gebruik voortaan een aparte, beperkt bevoegde smoke-testgebruiker i.p.v. het productieadmin-account.
6. **Volgende stap:** Gebruikers training en documentatie voor de pilot deelnemers.

---

## Commits

| Hash | Beschrijving |
|------|-------------|
| `e113cee` | fix(tests): case_id fixture uses authenticated session |
| `55a0d89` | fix(reports): deploy report + data recovery |
| `f542cff` | docs: ADR-0001 + RUNBOOK + browser smoke tests staging-first |
