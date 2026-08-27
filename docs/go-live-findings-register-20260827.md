# Go-live — 2026-08-27: Findings-register + rapportvlag (PR #65)

Status: **AFGEROND / LIVE** — zie `docs/deploy-plan-findings-register-report-flag.md` voor het plan.

## Uitrolverloop

- VPS-master op GitHub-master `016575bb` gezet (was stale/gedivergeerd; de reservebranch bleek achteraf al onderdeel van master — geen verlies).
- Pre-bestaand SpiderFoot-crashloop opgelost (weesproces hield poort 5001; 11 dagen down). Dienst weer `active`.
- Deploy via `scripts/production_rollout.sh --confirm DEPLOY-MASTER`:
  - preflight 22/22 (incl. `pip-audit` geen kwetsbaarheden);
  - dubbele encrypted backup (`iveras_backup_20260827_194603` en `..._194906`);
  - `alembic upgrade head` → `a2b3c4d5e6f7` (findings-migratie);
  - frontend-build OK, service herstart, health OK.

## Eindstaat productie

| Component | Status |
|---|---|
| App `/api/v1/health` | `{"status":"ok"}` |
| `osint-dashboard` / `spiderfoot` / `license-server` | `active` |
| Alembic head | `a2b3c4d5e6f7` |
| Kolom `findings.include_in_report` | aanwezig |
| `.deployed_sha` | `016575bb…` |

## Functionele verificatie (pilotzaak 00082 — Silas den van Veen)

Register `/cms/workflow/findings` → HTTP 200 (ingelogd admin). Vlag-semantiek bewezen op productie-DB (ADR-0001 optie b):

| `include_in_report` | Opgenomen in PV (van 3 actief) |
|---|---|
| `false` | 2/3 (uitgesloten) |
| `NULL` (standaard) | 3/3 |
| `true` | 3/3 |

Vlag teruggezet op `NULL` (geen resttoestand). Artefacten in `/opt/osint-dashboard/reports/pilot/` en `/reports/rollout/`.

## Openstaande punten (niet-blokkerend, zie #67)

- Echte PDF-diff per pilotzaak bij breder gebruik (deze pilot bewees de filterlogica; een echte pre/post-PDF-export is nog niet als artefact bewaard).
- Browser-UI-check door operator gewenst (register + exclude → PDF regenereert); endpoint is gedekt door CI.
- DR-rehearsal (twee operators) volgens `DR_DRILL_RUNBOOK.md` plannen.
- Issues #66 (lokale `start.sh`) en #67 (poll-grouping + `VERIFY_DEBUG`) buiten deze release.

## Operationele notitie (RLS)

Productie draait met Row Level Security; directe `psql`-queries vereisen
`SET app.bypass_rls='true'` (of `SET app.current_tenant=...`). Zonder deze SET
returnen queries lege resultaten — géén data-verlies. Zie `AGENTS_OPERATIONS.md`.
