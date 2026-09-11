# Design & Impact Plan — Onderzoek-detail-werkruimte binnen een Zaak

- Status: **v2 — herzien na onafhankelijke review; goedgekeurd met correcties.
  Nog GEEN code; dit document is de bouworderset.**
- Datum: 2026-09-11 (v1) / 2026-09-11 (v2, review-verwerking)
- Related: `ADR-0002-investigations-within-case.md`, `ADR-0005-research-actions-investigation-link.md`, `ADR-0002-impact-inventory.md`
- Beheer in dit document: secties 4 (fasedeling), 5 (besluiten), 6 (risico's)

---

## 1. Doel

Een **zelfstandig onderzoek-detailscherm** binnen een zaak, bereikbaar via het
bestaande `human_number` (`2026-00042-01`) én een technisch pad
`/cms/workflow/case/<case_id>/investigations/<investigation_id>`. Het scherm is
de **werkruimte van één onderzoek**: overzicht met titel/status/zaakcontext,
subjects betrokken via onderzoeksacties, gescheiden onderzoekgebonden
(🔗) en zaakbrede (🌐) acties, bevindingen, voortgangsaantallen en een
onderzoekgerichte activity/audit-tijdlijn, met links terug naar zaak, subject,
actie en finding. Binnen dezelfde zaak blijven _zaakbrede_ items herkenbaar
apart beschikbaar.

**Hard scoping-boundaries (ADR-0002/ADR-0005):**
- Bestaande nummers en historische acties blijven ongewijzigd (D3/D4).
- `investigation_id IS NULL` blijft zaakbreed (ADR-0005 D1/D5).
- Alleen **open** onderzoeken zijn nieuwe koppeldoelen (ADR-0005 D6).
- Rapportage blijft in deze fase standaard **zaakbreed** (ADR-0002 D5); het
  detailscherm bundelt wél de findings die via de actions-junction tot dit
  onderzoek behoren, zonder de rapportage/export aan te passen.
- Geen `findings.investigation_id`-kolom (ADR-0002 D6): bevindingen volgen hun
  actie en worden **read-time afgeleid** (zie 3.4).

---

## 2. Inventarisatie (huidige stand, geen wijzigingen mogen worden aangebracht)

### 2.1 Model `Investigation`
| Aspect | Waarde | Bron |
|---|---|---|
| Tabel/fields | `id, tenant_id, case_id, sequence_no, title, instructions, notes, status('open'\|'archived'), archived_at, created_by, created_at, updated_at` | `cms/models/__init__.py:2079-2171` |
| `human_number` | **read-time property** `f"{case.case_number}-{sequence_no:02d}"` → `2026-00042-01`, nooit opgeslagen | `:2151-2154` |
| Sequence | `investigation_seq_counters` (PK `tenant_id,case_id`), atomische allocatie in `cms/services/sequence_service.py` (`allocate_investigation_sequence_no`, `create_investigation`) — **nooit MAX()+1** | `:2198-2226`, `sequence_service.py:122-188` |
| Invarianten | `unique(tenant_id,case_id,sequence_no)`; immutable `sequence_no/case_id/tenant_id` via DB-triggers; composite FK zaak<->onderzoek | `:2091-2118`, migrations `bb1c2d3e4f5a7`, `dd1e2f3a4b5c7` |
| Status | Alleen `open`/`archived`; `archived_at` synchroon | `:2072-2076` |
| Gaten | **Geen** `owner/assignee`, `description/goal`, `started_at`, `completed_at`; **geen edit-route**; **geen detailpagina**; **geen onderwerp-relatie** | — |

### 2.2 Routes (allemaal `cms/workflow/routes.py` tenzij anders vermeld)
| Route | Methode | Functie | Auth | Renders |
|---|---|---|---|---|
| `/cms/workflow/case/<id>/investigations` | GET | `investigations_index` `:1087` | login + `ensure_case_access` (viewers OK) | `workflow_case_investigations.html` (read-only) |
| `/cms/workflow/api/case/<id>/investigations` | POST | `create_investigation` `:1109` | + `_investigator_required` | JSON/redirect |
| `/cms/workflow/api/investigations/<id>/archive` | POST | `archive_investigation` `:2652` | + `_investigator_required` | JSON/redirect |
| `/cms/workflow/api/investigations/<id>/restore` | POST | `restore_investigation` `:2693` | + `_investigator_required` | JSON/redirect |
| `/cms/workflow/api/case/<id>/actions/<aid>/link` | POST/DELETE | `link/unlink_action_to_investigation` `:1437/:1490` | + `_investigator_required` | JSON |
| `/cms/workflow/api/case/<id>/status` | GET | `case_status` `:1789` (scope: `investigation_id/number/title` per actie) | + `_investigator_required` | JSON |
| `/cms/workflow/api/case/<id>/run-action`, `/proposals`, `/actions/<aid>/start` | POST | actie-create path, `investigation_id` optioneel, `get_linkable_investigation` geldig | + `_investigator_required` | JSON |
| `/cms/workflow/case/<id>` | GET | `case_detail` `:929` — embedt `investigations_meta` + Step3-sectie | `_investigator_required` | template |
| `/cms/audit` | GET | `cms/routes/audit.py:14` — filter entity_type/action/user/case/search, **geen investigation-filter** | login + `senior_required` | `cms/audit/log.html` |

**Huidige gate-hiaten (wordt opgevuld door de centrale helper, zie 3.1):**
- Er bestaat **geen `ensure_investigation_access`**; authority is 100%
  `ensure_case_access` via `_get_writable_investigation` (`routes.py:2636-2649`)
  en `get_linkable_investigation` (`cms/services/action_scope.py:17-47`).
- Bestaand `archive`/`restore`-pad is alleen id-gebaseerd
  (`/api/investigations/<id>/archive`) en checkt `case_id` impliciet via
  `_get_writable_investigation`; PR1 herschrijft deze paden intern naar één
  expliciete `(case_id, investigation_id)`-helper als enige gate.

### 2.3 Templates & navigatie
- `_investigations_section.html` (100 regels) — cards met `human_number/status/
  title/instructions/created_by/created_at`, archive/restore-formulieren
  (alleen `can_write`). **Cards zijn GEEN links → geen weg naar een detailpagina.**
- `workflow_case_investigations.html` — standalone read-only page (zelfde partial),
  nergens gelinkt.
- `workflow_case_detail.html:588-698` — findings-groepen per actie, scope-badge
  `🔗 human_number` / `🌐 Case-wide`, scope-filter dropdown, writer-only
  `.scope-link` select naar link/unlink.
- `_workflow_js_config.html:35` — `const INVESTIGATIONS = {{ investigations_meta|tojson }}`,
  `OPEN_INVESTIGATIONS`; `_workflow_picker.html`, `_workflow_polling.html`,
  `_workflow_events.html` — client-side scope-select/badge/filter.
- `subjects/profile.html` (`cms/routes/subjects_list.py:388-423`,
  `subject_profile_api.py:716-861`) — OPEN-only `investigation_options`,
  run-action/propose ge-scoped.

### 2.4 Findings
- `WorkflowFinding` (alias `Finding`): **geen `investigation_id`** (ADR-0002 D6);
  scope loopt via `action_findings` (junction) → `research_actions.investigation_id`.
- `_findings_base_query` `routes.py:388` (case-wide), `case_status` `:1807-1833`
  (joinedload screenshots, `finding_actions`-map), `create_manual_finding` `:1746`
  (zaakbreed, `link_finding_to_manual_action` → `manual_entry` action).
- Template-groepering: findings onder hun actie (`action_groups` in
  `workflow_case_detail.html:604-698`); "Other findings" voor ongebonden.
  **Geen per-onderzoek-weergave, geen dubbeltelling-afhandeling.**

### 2.5 Audittrail
- `AuditLog`: `action, entity_type, entity_id, case_id, user_id, tenant_id,
  description, old_values, new_values, ip_address, timestamp`. `log_scope_audit`
  (`action_scope.py:55-83`) schrijft `scope_change` met old/new `investigation_id`.
- `/cms/audit` (senior+) filtert op case/entity_type/action/user/search.
  **Geen entity-`investigation`-filter; geen per-onderzoek-tijdlijn.**

### 2.6 RLS & tenant (doorwerkings-randvoorwaarde)
FORCE RLS op `investigations`, `research_actions`, `action_findings` en alle
tenant-tabellen (migration `f6a7b8c9d0e1`, matrix in
`tests/test_postgres_rls_matrix.py`). Elke query loopt door de tenant-GUC
(`before_request` `app.py:124-155`); directe ORM-inserts vereisen context of
`bypass_rls`. **De nieuwe detailroutes voegen geen tabel aan — geen RLS-migratie
nodig zolang er geen nieuwe kolom/tabel komt.**

### 2.7 Deploy, feature-flag, i18n, a11y/mobiel
- `scripts/update.sh`: backup → git pull → pip → build.mjs → alembic upgrade head
  → restart → health. **Geen automatische rollback**; alle 57 migraties hebben
  `downgrade()`.
- Feature-flag-patroon: **DB `FeatureFlag`** (`cms/models/__init__.py:4358-4384`)
  ontsloten via de **centrale featurecontrole** (`cms/services/registry.py:103-138`).
  Regels: routes/views schrijven **nooit** losse `FeatureFlag`-queries; zij roepen
  alleen de centrale controle aan (`feature_enabled('investigation_workspace', …)`).
- i18n: flask_babel, `{{ _('...') }}`, talen en/nl/de/fr; `translations/messages.pot`
  handmatig via `pybabel extract`; `.mo`-bestanden gecommit.
- A11y/mobiel: eigen custom CSS (`--bg-card`, `.btn-cms`, `.finding-item`,
  `.workflow-step`); geen `<dialog>`-patroon; case-detail heeft `@media`-columns;
  JS-build via `build.mjs`.

---

## 3. Ontwerp

### 3.1 Centrale investering: `ensure_investigation_access(case_id, investigation_id)`

Eén helper die **alle** lees- en schrijftoegang tot een onderzoek centraliseert en
wordt gebruikt door **detail, update én activity** (en archive/restore, zie 3.4
notitie):

```
def ensure_investigation_access(case_id: str, investigation_id: str):
    inv = Investigation.query.filter_by(id=investigation_id, case_id=case_id).first()
    if not inv:            # (id, case_id)-combinatie — nooit alleen op id
        abort(404)
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)          # bestaand patroon (cms/auth.py:411-432)
    # archive/soft-delete-semantiek EXPLICIET en identiek op alle paden:
    #  - detail/activity: tonen zolang geautoriseerd (ook archived, met badge);
    #  - update/link/unlink/run-action: 400 "archived/inactive" voor archived
    #    en niet-open (get_linkable_investigation, action_scope.py:17-47).
    return inv, case
```

- Mismatch-guard: get op `(id, case_id)`, 404 bij afwijking — nooit `id` alleen.
- Eén plaats voor de archive/archived-semantiek; geen duplicatie over routes.
- `can_write` blijft `_current_user_is_investigator()` (`routes.py:77-79`).

**Autorisatiematrix (review-vast, wordt in élke PR letterlijk getest):**

| Gebruiker | Case-toegang? | Detail (GET) | Update/activity-mutaties |
|---|---|---|---|
| Admin/OWNER tenant | ja | 200 | 200 (schrijven) |
| Investigator / Senior investigator | ja | 200 | 200 (schrijven) |
| **Junior investigator** | **ja (via case-access)** | **200 — leest gewoon** (NIET automatisch 403) | schrijven alleen als `_investigator_required` toestaat (huidige rolset: nee); leest wél activity-END scherm |
| Viewer | ja | 200 | 403 op schrijven |
| Elke rol, anderen tenant | **nee** | **nooit zichtbaar (403/404)** | 403/404 |
| Elke rol, geen case-access | nee | 404/403 conform bestaand `can_access_case`-beleid | 404/403 |

### 3.2 Routemodel (alleen primair, geen nieuwe tabel)
```
GET   /cms/workflow/case/<case_id>/investigations/<investigation_id>
      → workflow.investigation_detail      (read-only; via helper)
POST  /cms/workflow/api/case/<case_id>/investigations/<investigation_id>/update
      → titel/instructions/notes (case-scoped; via helper + _investigator_required)
GET   /cms/workflow/api/case/<case_id>/investigations/<investigation_id>/activity
      → onderzoekgerichte Activity/Audit (via helper)
HERBRUIK  archive/restore, link/unlink, run-action/proposals/start
      (nieuwe paden id-gebaseerd blijven compatibel; intern helper gebruiken)
```
- **URL gebruikt technische id; het scherm toont `human_number`**
  (`2026-00042-01`). `human_number` is read-time afgeleid en geen URL-slug.
- Update-mutatie: `@_investigator_required` + helper; `AuditLog.log(action='update',
  entity_type='investigation', old_values/new_values, case_id)` + commit **één tx**.

### 3.3 Schermopbouw `workflow_investigation_detail.html`
Secties, van boven naar beneden (hergebruik bestaande componenten):

1. **Breadcrumb met human_number**: Dashboard › Cases › `case.case_number` ›
   `inv.human_number` (`2026-00042-01`). Header: `human_number` groot, `title`,
   status-badge (`open`/`archived`), `created_by`, `created_at`/`updated_at`.
2. **Overzicht**: `instructions` + `notes` (pre-wrap); **edit** (titel/instructions,
   `can_write`) via `<details>`/modal met a11y. Archief/Herstel-formulieren hergebruikt.
3. **Zaakcontext**: link terug naar `workflow.case_detail`, zaaknummer, eventueel
   zaak-status — volledig uit `case`.
4. **Subjects betrokken via onderzoeksacties**: subjects van acties van dit
   onderzoek (`ResearchAction.subject_id`), decrypt/format als
   `case_detail:987-1023`, miniatuur kaartjes, link naar
   `/cms/subjects/<id>/profile`. **Geen noem "gekoppelde subjects": er bestaat
   geen investigation-subject-relatie.** Toon alleen de afgeleide betrokkenheid.
5. **Onderzoeksacties (🔗)** — **primair blok**: acties met
   `investigation_id == inv.id`, gegroepeerd per actie met hun findings
   (hergebruik `action_groups` + `_finding_item.html`). Header: teller,
   status-badges.
6. **Zaakbreed (🌐)** — **secundair, apart blok**: acties met
   `investigation_id IS NULL` van dezelfde case. **Standaard ingeklapt/compact**
   (`<details>`/toggle), opsomming met badge, nooit de hoofd-focus.
7. **Actie-starter** binnen dit onderzoek: herbruik `ACTION_REGISTRY`-grid,
   `run_action(actionType, …, investigationId)` — nieuwe acties hier default 🔗.
8. **Voortgang + Activity/Audit**: **geen kunstmatig percentage.** Toon de status
   (`open`/`archived`) en **aantallen** proposals/running/completed/failed van de
   🔗-acties (+ losse tel zaakbreed). Daaronder de Activity-tijdlijn (3.5).
9. **Links terug**: elke actie/finding → `workflow.case_detail` met die actie/
   finding; subjects → profiel; zaak → case_detail.

### 3.4 Findings — read-time afleiding en dubbeltelling

```
Finding ∈ onderzoek ⇔ ∃ action_findings(af.finding_id=f.id)
                      JOIN research_actions ra ON ra.id=af.action_id
                      WHERE ra.investigation_id = <inv.id>   (read-time; geen kolom)
```

Bespeling:
- **Eén finding kan via meerdere acties in meerdere onderzoeken zichtbaar zijn.**
  Toon zo'n bevinding in elk onderzoek waarin hij afgeleid wordt, met label
  **"Gedeelde bevinding"** (shared-finding badge).
- **Binnen één onderzoek geen dubbeltelling:** een finding die via meerdere 🔗-acties
  van *hetzelfde* onderzoek afgeleid wordt, verschijnt exact één keer (dedup op
  `finding_id` binnen de onderzoek-blok), met in de metadata alle actie-verwijzingen.
- Bewijs in tests: een finding gekoppeld aan actie A (onderzoek X) én actie B
  (onderzoek Y) verschijnt in X en Y (elk eenmaal), nooit dubbel binnen X.

### 3.5 Activity/Audit — onderzoekgericht

Activity omvat **alleen** deze categorieën (niet alle zaakbrede auditregels):
1. **Wijzigingen aan het onderzoek zelf** (`entity_type='investigation'`,
   `entity_id=inv.id`): create/update/archive/restore.
2. **Acties die momenteel aan dit onderzoek gekoppeld zijn**
   (`entity_type='research_action'` met `research_actions.investigation_id == inv.id`
   op read-tijd): create/start/archive/restore.
3. **Scope-wijzigingen waar old óf new `investigation_id == inv.id`**
   (`action='scope_change'`, `old_values`/`new_values.investigation_id`): link
   in, unlink uit — inclusief historische linkverwijdering die nu "leeg" is.
4. **Findings afgeleid via die acties** (3.4): hun create/verify/screenshot/
   comment/archive-entries, met "Gedeelde bevinding"-duiding waar van toepassing.

Query-aanpak: `AuditLog`-rijen oplossen via `(case_id)` + entity/action-filter,
daarna client/server-side op bovenstaande scope filteren; **geen
`investigation_id`-kolom op `AuditLog`** (besteand schema houden).

---

## 4. Gefaseerd plan in 4 PR's (+ review poort)

Rollout: feature is **OFF default**. Per tenant expliciet activeren via de
**bestaande centrale featurecontrole** (`cms/services/registry.py`); routes/views
doen **geen losse `FeatureFlag`-queries**. `feature_enabled('investigation_workspace')`
wordt centraal gelezen; OFF → `investigation_detail`/`activity` laden 404/redirect,
case-detail en bestaande list/archive/restore blijven onveranderd.

**Per-PR-verplichtingen (alle PR's):** i18n (nl/en keys) én a11y-basis én mobiele
layout zijn **acceptatie-eisen binnen élk PR**, en **élke PR die een route of query
toevoegt draagt zijn eigen PostgreSQL/RLS-isolatietest** in dezelfde PR
(via het `integration-postgres`-pad in `.github/workflows/ci.yml`) — niet
uitgesteld naar PR4.

| # | PR | Scope | Bestanden | Risico's | Tests (altijd incl. PG) | Flag/rollback | Acceptatie |
|---|---|---|---|---|---|---|---|
| **1** | Detail RO + navigatie + centrale helper | Route `investigation_detail` (read-only) + template + breadcrumb + case-cards als link + helper `ensure_investigation_access` (+ archive/restore intern naar helper) | `cms/workflow/routes.py`; `workflow_investigation_detail.html`; `_investigations_section.html` (link); `workflow_case_investigations.html` (link); i18n keys; `tests/test_investigation_detail_access.py` (+ PG-variant/RLS) | Link-breuk case-detail; helper-refactor (archive/restore) geeft straks maar één gate; 404-mismatch | **`test_investigation_detail_access.py`** (matrix uit 3.1: junior-lezen 200, viewer 200, geen case-access 403/404, andere tenant nooit) + **PG-isolatie**: detail tonend/verborgen onder FORCE RLS; wrong-case 404; human_number-rendering; links terug | Flag OFF = 404; ON = pagina | Matrix exact groen; human_number klopt; helper gebruikt door detail+archive/restore |
| **2** | Update (case-scoped) + edit UI + audit | `POST /api/case/<cid>/investigations/<iid>/update` + edit-modal + audit | `routes.py` (update + audit via helper), `workflow_investigation_detail.html`, `_workflow_js_config.html`, audit partial; `tests/test_investigation_update.py` (+PG) | FK/RLS op update; audit-atomiciteit; XSS titel (tojson+DOM API) | **`test_investigation_update.py`**: auth-matrix (403/404/ander-tenant), old/new audit, i18n-key, **PG**: update-RLS + atomiciteits-controle | Flag-gate update; downgrade = prior state | Editing werkt; audit toont old/new; geen cross-tenant write |
| **3** | 🔗/🌐 blokken + findings + subjects | Data-uitbreiding detailroute (actions, findings via junction, subjects) + primaire/secundaire blokken + "Gedeelde bevinding"-badge + dedup | `routes.py` (detail-data), `workflow_investigation_detail.html` (blokken), `_finding_item.html` (links), i18n; `tests/test_investigation_workspace_data.py` (+PG) | N+1 (eager-load); dubbeltelling-findingen; afleiding traag op grote zaak | **`test_investigation_workspace_data.py`**: scope-filter 🔗/🌐, findings-afleiding + **dedup + gedeelde-bevinding in 2 documenten**, subjects "betrokken via acties", **PG**: junction-query onder FORCE RLS toont/isoleert correct | Flag OFF = case_detail onveranderd (blokken alleen in detail) | 🔗-blok primair; 🌐 secundair ingeklapt; findings éénmaal per onderzoek; gedeelde-badge klopt |
| **4** | Activity + geïntegreerde acceptatie | Activity-tijdlijn endpoint + voortgangsaantallen (geen %) + laatste integratie/acceptatie-tests | `routes.py` (activity), `workflow_investigation_detail.html` (tijdlijn-partial), `_workflow_events.html`-achtig, `tests/test_investigation_activity.py`, `tests/test_postgres_investigation_workspace.py`, `ci.yml` | Audit zonder investigation-kol → case+filter combineren; performance tijdlijn | **`test_investigation_activity.py`** + **PG-integratie** (tijdlijn-under-FORCE-RLS) + volledige acceptatie-run (hele flow door viewer/junior/investigator) | Flag-gate activity; downgrade = standaard case-filter | Tijdlijn omvat precies de 4 categorieën (3.5); voortgang = aantallen + status; a11y/mobiel/i18n-eisen groen |

### 4.1 Overkoepelende acceptatiecriteria (alle PR's)
- Bestaande nummers/acties/findings onaangetast (vergt risico-vrije run huidige suites).
- `human_number`-weergave correct; `NULL` blijft zaakbreed; open-only koppeldoelen.
- **Geen** `findings.investigation_id`-kolom; geen nieuwe tabel/migratie tenzij expliciet.
- Rapportage/export volledig ongewijzigd (ADR-0002 D5).
- i18n (nl/en), a11y-basis, mobiel en **de relevante PG/RLS-test** zijn per PR
  opgenomen; elke PR los merge-over naar green CI.

---

## 5. Besluiten (vastgelegd na onafhankelijke review)

| # | Besluit | Verwerking in dit plan |
|---|---|---|
| 1 | Findings read-time via `action_findings`; géén `findings.investigation_id`; één finding in meerdere onderzoeken zichtbaar; **"Gedeelde bevinding"**; geen dubbeltelling binnen één onderzoek | 3.4 + PR3-tests |
| 2 | Zaakbrede acties = **apart, secundair blok**, standaard ingeklapt/compact; **geen** generieke case-weergave via `?scope=case\|all` (onderscheid zaak/onderzoek blijft) | 3.3.6 + PR3 |
| 3 | Feature-flag **OFF default**, per tenant via **centrale featurecontrole**; géén losse `FeatureFlag`-queries in routes | 4 intro |
| 4 | URL = technische id (`/case/<case_id>/investigations/<investigation_id>`); scherm toont `human_number` | 3.1/3.2 |
| 5 | Activity onderzoekgericht (4 categorieën, niet alle zaakbrede audit) | 3.5 + PR4 |
| 6 | Centrale helper `ensure_investigation_access(case_id, investigation_id)` voor detail, update, activity (+ archive/restore) | 3.1 + PR1 |
| 7 | Update case-scoped: `/api/case/<cid>/investigations/<iid>/update` | 3.2 + PR2 |
| 8 | Junior met case-toegang leest (geen automatische 403); matrix expliciet getest | 3.1 + PR1-test |
| 9 | PG/RLS-tests per PR, niet uitgesteld | 4 intro + PR1..4 |
| 10 | "Subjects betrokken via onderzoeksacties" (geen "gekoppelde subjects") | 3.3.4 + PR3 |
| 11 | Geen voortgangspercentage; toon aantallen + status | 3.3.8 + PR4 |
| 12 | PR4 versmalt: alleen Activity + integrale acceptatietests; a11y/mobiel/i18n = acceptatie per alle PR's | 4 tabel |

---

## 6. Risicomatrix
| Risico | Implicatie | Beperking |
|---|---|---|
| Junior/viewer leesrecht onduidelijk vs. case_detail-gate | detail-page is read-only; junior leest als case-toegang geldt (géén `_investigator_required` op GET) | matrix (3.1) + PR1-test letterlijk |
| Helper-refactor (archive/restore) introduceert gedragswijziging | alleen gecoördineerd in PR1; oude gedrag (4x `_get_writable_investigation`) vervalt contant | identieke semantiek; bestaande archive/restore-tests blijven groen |
| N+1 bij findings/subjects | traag op grote zaak | eager-load joinedload + één activity-query |
| RLS/tenant-lek bij query | alle nieuwe query's door route+helper met tenant-GUC | PG-isolatietest IN élke PR (NOBYPASSRLS rol) |
| Titel/notes XSS | tojson escapen + DOM API (ADR-0005 D9-patroon) + audit old/new | PR2-code-review |
| Dubbeltelling/gedeelde findings verkeerd | misleidende counts | dedup+finding-badge tests PR3 (PG + sqlite) |
| Activity te breed (alle zaakbrede regels) | tijdlijn wordt ruis | strakke categorie-lijst 3.5 + PR4-test |
| Migration drift | geen nieuwe tabellen; alleen optionele index | migratie optioneel; downgrade aanwezig |

---

## 7. Implementatie-aanwijzingen voor de bouwer (niet-blokkerend)

- Volgorde PR1 → PR4, elke PR los merge-over met groene CI (incl. eigen PG-test).
- Eén helper als enkele bron van toegang tot onderzoek — **altijd** via
  `ensure_investigation_access(case_id, investigation_id)`; geen ad-hoc
  `_get_writable_investigation` bij nieuwe code.
- Flag uitsluitend via centrale controle; geen losse `FeatureFlag`-queries.
- `?scope=case|all` **niet** introduceren; het detailscherm is en blijft
  onderzoek-gericht (🌐-blok alleen als compacte secundaire referentie).

Vastgesteld door: Ivan (owner) na onafhankelijke review van v1; v2 legt de
besluiten en correcties voor implementatie vast.