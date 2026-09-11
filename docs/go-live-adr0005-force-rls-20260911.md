# Go-live — 2026-09-11: ADR-0005 FORCE RLS op research_actions + action_findings (pilot en operationele afronding)

Status: **AFGEROND / LIVE** — zie `docs/ADR-0005-research-actions-investigation-link.md` voor het ADR.

Dit record bewijst dat ADR-0005 functioneel én operationeel is afgerond in productie: de
migratie (FORCE RLS op `research_actions` + `action_findings`), de functionele
onderzoek-koppeling (zaakbreed vs. onderzoekgebonden acties, link/unlink, audit), én de
RLS-posture (rol `osint`, non-superuser, FORCE RLS).

## Deploy-verloop

| Stap | SHA / waarde |
|---|---|
| Migratie | `f6a7b8c9d0e1` «FORCE RLS voor research_actions + action_findings (ADR-0005 closure)» |
| Merge | PR #154 (deploy via `scripts/update.sh` op `cms-vps`) |
| Productiebug | `cms/routes/subjects_list.py:417` — SQLAlchemy 2.0 `Row` string-index (`c["id"]`) → `TypeError` (alleen triggert met 2+ subjects in de tenant) |
| Fix | PR #155: `Row` attribute-access (`c.id`) + regressietest, merge → `a99b084` |
| Regressietest | `tests/test_research_action_link_ui.py::TestSubjectProfileScopeUi::test_profile_relation_candidates_serialize_sqlalchemy_rows` (SQLite-suite) |
| PG-dekking fix | `tests/test_postgres_subjects_profile_row.py` — nieuw, draait in CI-job `integration-postgres` van `a99b084` af |
| Deploy met fix | `a99b08425ee3179ac1ae370a4288113cf0495bd6` via `scripts/update.sh` op `cms-vps` → health OK |

## Productie-omgeving (gemeten)

- App-rol: `osint` — `rolsuper=False`, `rolinherit=True`, `rolbypassrls=False`.
- `research_actions`, `action_findings`, `investigations`: `relrowsecurity=True` én `relforcerowsecurity=True` met `tenant_isolation`-policies.
- Feature-flags tenant `3a169c92-04a2-48f9-be1b-1fcf930c0f0f`: `subject_first_investigations=ON`, `paid_channels=ON`.
- `app.py:31` loadt `.env` (productie-DB); RLS-context wordt per request gezet via `before_request` → GUC `app.tenant_id` / `app.bypass_rls`.

## Pilot (wegwerp-case, `app.test_client` met RLS-context — geen wachtwoord-login)

Volledige 8-stappen-pilot op productie-DB; alle stappen 1–7 **PASS**.

| Stap | Resultaat |
|---|---|
| 1. Case + subject (`POST /cms/workflow/case/new`, `302` + detail `200`) | PASS |
| 2. Onderzoek-aanmaken (`POST api/case/<id>/investigations`) | PASS |
| 3. Acties: zaakbreed + onderzoekgebonden (`manual_entry`/`proposal`) | PASS |
| 4. Scope-serialisatie: status-API `investigation_id`/`investigation_title`/`investigation_number`, subject-profile badges 🌐/🔗 | PASS |
| 5. Link/unlink + idempotentie; audit-trail (`entity_type=research_action`, `entity_id`) | PASS |
| 6. Audit-pagina (`GET /cms/audit`) toont research_action-entries | PASS |
| 7. RLS-bewijs op ruwe connectie (rol `osint`, FORCE RLS): **zonder GUC 0 rijen zichtbaar; met tenant-GUC 2 rijen** | PASS |
| Step 8 | PASS (zie noot hieronder) |

> Opmerking stap 8: de opruimfunctie in de eerste `/tmp/pilot_run.py` crashte op een
> onnodige `from cms.models import WorkflowClient` (module importeert dat symbool niet;
> het leeft als alias in `cms.workflow.models`). De wegwerp-case werd apart opgeruimd
> via de deterministische sweep `pilot_cleanup*.py` (bewezen geslaagd, zie tellingen).
> Het hulpmiddel is daarna genormaliseerd en versioned in
> `scripts/pilot_adr0005_rls.py` — spookimport verwijderd, deterministische FK-veilige
> cleanup in plaats van FK-closure (die faalt op stale FK-metadata in deze DB).
> De eerste herbewijs-draai van het versioned script legde vervolgens een tweede
> opruimfout bloot: `invoices` refereerden de wegwerp-`client`, dus `DELETE FROM
> clients` gaf een FK-violation. Fix (PR #157): `invoice_items` en `invoices`
> vóór `clients` verwijderen, met tuple-parameters zodat psycopg2 `IN (...)` rendert
> i.p.v. `ARRAY[...]`. Twee half-crash-runs lieten daardoor residu achter
> (cases `5aacdd4e-…` en `1b589af6-…`), opgeruimd via `pilot_cleanup5.py` /
> `pilot_cleanup4.py` (zie tellingen).

## Tellingen en cleanup-verificatie (tenant 3a169c92-…)

Gemeten met GUC `app.tenant_id` ingesteld (zonder GUC verbergt RLS alles; dat is
wél het correcte gedrag — zie RLS-bewijs). Referentie-basis vóór de pilot:
**105 cases / 284 research_actions**.

| Meetpunt | cases | research_actions | Opmerking |
|---|---|---|---|
| Basis (voor pilot) | 105 | 284 | alle echte productie-data |
| Na crash-run 1 | 105 | 284 | + pilotelementen (binnen dezelfde tel) |
| Determin. cleanup run 1 (`pilot_cleanup2.py`) | 104 | 282 | pilotelementen weg; −2 acties, −1 case |
| Na pilot-run 2 (wegwerp) | 105 | 284 | + pilotelementen |
| Determin. cleanup run 2 (`pilot_cleanup3.py`) | 104 | 282 | pilotelementen weg; −1 case, −2 acties |
| Re-proof run A (crashte bij FK `clients←invoices`) | 105 | 284 | residu case `5aacdd4e-…` |
| Re-proof run B (crashte bij `ARRAY[...]`-IN) | 106 | 286 | residu case `1b589af6-…` |
| Determin. cleanup (`pilot_cleanup4.py` + `pilot_cleanup5.py`) | 104 | 282 | beide residu-cases weg |

Pilot-residu na alle cleanups = **all-zero** (cases, investigations, subjects, clients,
research_actions, subject_relations, audit-entries met `PILOT-RLS`): geverifieerd met een
vers connectie mét GUC (`verify_cleanup.py` → `OVERALL: OK`). De 104 niet-pilot cases en
282 niet-pilot acties bleven in alle metingen intact.

## RLS-gedrag als design-kenmerk

Directe `psql`-queries zonder `SET app.tenant_id=…` (of `app.bypass_rls='true'`) retourneren
lege resultaten — dit is **de beoogde FORCE RLS**, geen data-verlies. Het is ook wat
stap 7 bewees (0 zichtbare rijen zonder GUC, 2 met GUC).

## Resterende bevindingen (niet-blokkerend)

- `docs/HEALTH_LATENCY_*` en nginx: `/api/v1/health` via directe poort `:5000` OK; nginx heeft geen health-locatie (eigen config-keuze, geen app-probleem).
- Browser-level UI-wandeling (Playwright) door operator staat nog uit; endpoint-dekking in CI is aanwezig.