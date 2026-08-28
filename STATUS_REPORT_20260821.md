# ADR-0001 Subject Profile Pilot — Status Report
**Datum:** 21 augustus 2026

## Samenvatting
De Subject Profile pilot is operationeel op productie. Alle issues (#53–#58) + PR59 zijn afgerond. Security review en browser smoke tests zijn geïmplementeerd. De search-schaalbaarheid is nu ook opgelost.

## ⚠️ Security-incident: TOTP-secret blootgesteld
Het TOTP-secret van het productie-testaccount is per ongeluk in deze statusrapporten en chat-context opgenomen in plaintext. **Dit werd als gecompromitteerd behandeld.**

**Acties:**
- [x] TOTP secret geroteerd in productie-DB (21-08-2026)
- [x] 2FA opnieuw instellen in authenticator app — bevestigd werkend
- [ ] Controleren of secret in git-history of logs staat (verwijderen indien nodig)
- [x] Dit incident registreren

De oude waarde is uit dit rapport verwijderd.

## Afgerond (lokaal + git)

| # | Omschrijving | Commit | Status |
|---|---|---|---|
| 1 | Search schaalbaarheid: SQL ILIKE i.p.v. O(n) decrypt+filter | `178ed19` | ✅ Gepusht |
| 2 | FTS fix: encrypted fields verwijderd uit subject search | `178ed19` | ✅ Gepusht |
| 3 | Composite indexes: tenant isolation + name search | `178ed19` | ✅ Gepusht |
| 4 | Key rotation: multi-key fallback, CLI, 12 tests | `abbb2fe` | ✅ Gepusht |
| 5 | CSP nonce fix: 3 inline scripts | `4d60dd1` | ✅ Gepusht |
| 6 | Browser smoke tests: requests.Session + Origin headers | `dc9020e` | ✅ Gepusht |
| 7 | Security review #55, #56, #58: tenant check merge-bug | `766b0ae` | ✅ Gepusht |
| 8 | Issue #53: no_autoflush 10 locaties | `205aed5` | ✅ Gepusht |
| 9 | Issue #54: addition field in JS wfSerializeAllAddresses | `2d2b3c0` | ✅ Gepusht |
| 10 | Issue #55: CASCADE FKs op junction tables | `ed1dbd8` | ✅ Gepusht |
| 11 | Issue #56: Start Research Action op beide views | `f07ffb4` | ✅ Gepusht |
| 12 | Issue #57: encrypted search | `74c73d6` → vervangen door `178ed19` | ✅ |
| 13 | Issue #58: merge endpoint + UI | `74c73d6` | ✅ Gepusht |
| 14 | PR59: encryptie-diagnostiek + herstelbaarheid | `b865f82` | ✅ Gepusht |

## Nog te doen

| # | Omschrijving | Status |
|---|---|---|
| 1 | **Deploy naar VPS** — search fix (`178ed19`) + Alembic migratie `c3d4e5f6a7b8` | ✅ Gedeployd 21-08 |
| 2 | **Pilot evalueren** — feature flag staat aan, echte usage testen | ⏳ |
| 3 | **Documentatie bijwerken** — ADR-0001, deployment docs, key rotation runbook | ✅ `f542cff` |
| 4 | **Cases aanmaken op VPS** — nodig voor case-detail browser tests (2 skips) | ✅ Data hersteld: 26 subjects, 8 cases |

## Test-resultaten

### Volledige regressiesuite (21-08-2026)
| Suite | Resultaat |
|---|---|
| **Totaal** | **736 passed, 25 skipped, 0 failed** |

### Onderdeel-suites (voor referentie)
| Suite | Resultaat |
|---|---|
| Key rotation (12 tests) | ✅ 12/12 |
| Core + integrity (28 tests) | ✅ 28/28 |
| Encryption integrity (8 tests) | ✅ 8/8 |
| Soft-delete search (13 tests) | ✅ 13/13 |
| Browser smoke (15 tests) | ⏭️ 6 passed, 9 skipped (server nodig) |

### Productie smoke tests (VPS)
| Suite | Resultaat |
|---|---|
| Production browser smoke | ✅ 7/7 passed, 2 skipped (geen cases) |

## Productie-state (laatst bekend)

| Metrisch | Waarde |
|---|---|
| Alembic head | `c3d4e5f6a7b8` (geüpgraded 21-08) |
| Active encrypted fields | 76/76 OK |
| Key rotation status | Actief (`CMS_ENCRYPTION_KEYS` met fallback) |
| Feature flag global | `subject_first_investigations_global = 1` |
| Feature flag tenants | Beide tenants enabled |
| TOTP secret (DB) | Geroteerd 21-08-2026 (oud secret gecompromitteerd) |
| Gunicorn workers | 4 |
| SpiderFoot | Gestopt (memory) |

## Deployment

Deploy uitgevoerd op 21 augustus 2026 via `development@joost.iveras.com`.

| Stap | Status |
|---|---|
| `git pull origin master` | ✅ `178ed19` |
| `flask db upgrade head` | ✅ Migratie `c3d4e5f6a7b8` toegepast |
| `./start.sh restart` | ✅ Gunicorn herstart |
| Health check | ✅ Alle services OK |
