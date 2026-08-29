# ADR-0002 — Investigations within a Case (Zaak → meerdere Onderzoeken)

- Status: **Proposed (draft for approval — PR 1)**
- Date: 2026-08-29
- Deciders: Ivan Versteegh (owner), Perry Couprie (review), codebase
  verification (OpenCode PR1 inventory)
- Related docs: `ADR-0002-impact-inventory.md` (evidence),
  `ADR-0001-subject-first-investigations.md` (prior model)

> **Scope of this PR (PR 1):** ADR + impact inventory only. No code, no
> migration, no new screens, no renumbering of existing records. This document
> fixes the design decisions; PRs 2..6 implement them incrementally behind an
> explicit approval per PR.

## Context

A "Zaak" (case) can contain multiple "Onderzoeken" (investigations). Today the
model has no such container:

- A `Case` is the outer unit with a unique human `case_number`
  (`YYYY-XXXXX`, e.g. `2026-00042`), unique per tenant via
  `uq_tenant_case_number` (`cms/models/__init__.py:630`).
- The workflow UI currently **labels a `Case` as an "Investigation"**
  (dashboard "Investigations", `workflow_pv` heading "Investigation report",
  subject-profile "Investigation" tab, invoice label "Onderzoek: …"). This
  label collision must be resolved; the new model owns the term
  "Onderzoek/Investigation" going forward, and case-level UI text migrates to
  "Zaak/dossier" where ambiguous.
- `ResearchAction` is a research action **inside a Case**: UUID `id` + `label`,
  no sequential number, no `sequence_no` column
  (`cms/models/__init__.py:1936`).
- `parent_case_id` / `child_cases` is the **existing case hierarchy**
  (`cms/models/__init__.py:~668-707`) and is **reserved**: it must NOT be
  reused for the new container and must NOT be auto-migrated.

### Hard constraints carried from the request

1. Never reuse `parent_case_id` for the new container; never rename
   `ResearchAction`.
2. Human number for an investigation is derived from
   `case.case_number + investigation.sequence_no`, e.g. `2026-00042-01`.
3. Numbers (case numbers and investigation sequence numbers) are **immutable
   after issuance and never reused**, even after archiving.
4. PR 2 hard requirements (see D2 / Phasing): never `MAX()+1`; a DB unique
   constraint per `(tenant_id, case_id, sequence_no)`; atomic counter/UPSERT
   tested on **both PostgreSQL and SQLite**; concurrency, RLS, migration and
   rollback tests.

## Decision

### D1. Recommended model: a first-class `Investigation` under `Case`

A new entity `Investigation` (`investigations` table) is the "Onderzoek"
container **inside** a `Case`:

- `id` UUID (technical identity; PK)
- `tenant_id` (foreign key, NOT NULL — see D8)
- `case_id` (foreign key -> `cases`, NOT NULL)
- `sequence_no` integer, per-case sequential (`1`, `2`, …) — see D2/D3
- `title` (invariant name of the investigation, e.g. "Bedrijfsuitdraai ACM")
- `status` (e.g. `open` / `archived`), `instructions`/notes, timestamps as
  needed
- No own `number` column: the human number is **computed** from
  `case_number` + `sequence_no` (single source of truth; prevents drift).

`ResearchAction` is **not renamed** and keeps its UUID identity; `parent_case_id`
is **not reused**. This keeps existing action/chaining semantics untouched.

### D2. Identity and human numbering

- Technical identity remains **UUID** everywhere (`Case`, `Investigation`,
  `ResearchAction`).
- Human number = `case.case_number` + zero-padded `sequence_no` of the
  investigation within that case:
  `2026-00042` + `01` → **`2026-00042-01`**.
- `sequence_no` is stored as an integer; the derived full number is produced at
  read time by one helper (e.g. `investigation.human_number` property).
- Because `sequence_no` is scoped per `(tenant_id, case_id)`, the pair
  `(tenant_id, case_id, sequence_no)` is unique — enforced by a DB unique
  constraint (never by `MAX()+1`).

### D3. Immutability and no reuse

- Once a `sequence_no` is **issued**, it is never changed and never reused,
  even after the investigation is archived. Gaps (deleted/failed sequences)
  stay gaps.
- Case numbers are immutable after issuance (see D4 policy) and never reused
  for a new case.
- Impact: numbering must be issued **atomically** (see PR 2 hard requirements;
  `MAX()+1` is unsafe under concurrency and is explicitly forbidden).

### D4. Case-number change policy — immutability applies to administrators too

An issued `case_number` can **not** be changed in substance, **including by
administrators**. A correction of a wrongly issued/typed case number is not
done by overwriting `case_number`; instead it is recorded as an **auditable
alias / correction note** linking old and new number, while the stored
`case_number` stays the immutable identifier quoted in all derived
references.

Reason: the investigation reference is **derived** as
`<case_number>-<sequence_no>`. If the case number were changed, the reference
of every existing investigation would silently change as well — which violates
the rule that issued numbers are immutable and never reused (D3).

Current behavior is inconsistent and unvalidated:

- Workflow `case_edit` writes the submitted `case_number` directly
  (`cms/workflow/routes.py:1046`) — arbitrary changes are possible, no
  migration-based renumber.
- Legacy CRUD edit (`cases_crud.py`) does **not** allow changing
  `case_number`.
- `case_number` uniqueness is DB-enforced
  (`uq_tenant_case_number`, `cms/models/__init__.py:630`), but format and
  immutability are not policed; there are **no tests** asserting
  `generate_case_number()` format/uniqueness.

**Consequence (applies to DB + UI):** `case_number` becomes read-only after
issuance; the workflow `case_edit` path (`cms/workflow/routes.py:1046`) may
no longer overwrite it. Any correction needs an audit-logged alias/correction
note (PR 2/3), never a write to `case_number`. This keeps the derived
investigation identity `2026-00042-01` stable.

### D5. Reporting policy — decision point to confirm

- **Default (recommended for this PR round):** reporting stays **case-wide**
  and existing reports/PV/PDF/exports are **unchanged**.
- Investigation-specific reporting/filtering is a later, opt-in extension
  (PR 5) — no schema or template change in PR 2..4 forces it.

### D6. Findings policy — decision point to confirm

- A `Finding` is **not** blindly bound to a single `investigation_id`: a
  finding can legitimately link to actions from **multiple** investigations
  (via the `ActionFinding` junction, `cms/models/__init__.py:2033`).
- Two open options, to be decided with stakeholders:
  - (a) manual findings stay **case-wide**, with investigation as an optional
    tag/metadata;
  - (b) manual findings become **investigation-bound** for new records, with
    the junction preserved for cross-investigation evidence.
- Consequence: **do not add a NOT NULL `investigation_id` to `findings`** in
  any PR without the confirmation of (a)/(b) and a plan for existing rows.

### D7. Authorization: inheritance, no standalone access

- `Investigation` **inherits case access**. No standalone access surface
  outside the case: every investigation read/write is gated by the owning
  case through the existing permission helpers
  (`can_access_case` / `ensure_case_access` /
  `accessible_case_ids` in `cms/workflow/routes.py` and
  `cms/routes/subjects_list.py:334-354`).
- Role model is unchanged: tenant permissions apply at case level first.

### D8. RLS / tenant isolation

- `investigations` carries `tenant_id` (NOT NULL) and is subject to
  **FORCE RLS** with the same pattern as `cases`/`findings`: row security
  policies filter by tenant context; direct DB access requires
  `SET app.bypass_rls = 'true'` (see migration f0a1b2c3d4e5 and
  `AGENTS.md`; confirmed enforced set in
  `tests/test_postgres_integration.py:48-57`).
- Every insert/query must set the explicit tenant context — the counter/UPSERT
  in PR 2 must run **inside the tenant boundary**, never global.

### Open decision points (summarized for explicit sign-off)

| # | Point | Recommended default |
|---|-------|---------------------|
| D4 | Case-number changes after issuance | **Settled — not a decision point:** immutable for everyone, admin included; corrections via auditable alias/correction note (never overwrite `case_number`) |
| D5 | Reporting scope | Case-wide unchanged; investigation-specific later |
| D6 | Manual findings binding | Keep case-wide option (a); no forced `investigation_id` |

## Consequences

- All existing case-level behavior (creation, detail, actions, findings,
  reporting, invoicing, hierarchy) keeps working **without change** through
  PR 2..4; `investigation_id` is added to `research_actions` as a *nullable*
  FK for new work (PR 4).
- The workflow UI gradually re-labels case-level "Investigation" →
  "Zaak/dossier" to free the term "Onderzoek/Investigation" for the new
  container (naming sweep tracked in the impact inventory).
- Backfilling historical data into the new model is deferred to **PR 6**
  (feature-flag rollout), so the initial model can ship with **new** records
  only.

## Phasing (each PR with CI + explicit approval)

1. **PR 1 (this):** ADR + impact inventory. No functional change.
2. **PR 2 — datamodel + atomic number issuance:**
   - New `investigations` table (columns per D1), migration up/down.
   - Unique constraint `(tenant_id, case_id, sequence_no)` (D2).
   - **Atomic** sequence issuance — never `MAX()+1`; counter/UPSERT (e.g.
     `INSERT … ON CONFLICT` / transactional counter) tested on **PostgreSQL
     AND SQLite**.
   - Concurrency test (parallel issuance), RLS test (tenant isolation +
     FORCE RLS), migration + rollback tests, `test_postgres_integration.py`
     head literal updated in lockstep.
3. **PR 3 — investigation screen within a case:** list/create/archive an
   investigation inside the case detail; no other changes.
4. **PR 4 — ResearchActions link to investigation:** nullable
   `research_actions.investigation_id`; action creation surfaces an
   investigation picker (reuse `_workflow_picker.html` pattern).
5. **PR 5 — findings/reporting:** per decisions D5/D6 (investigation-scoped
   filters/reports, findings policy).
6. **PR 6 — historical backfill + rollout:** feature-flag rollout,
   backfill/renumbering only after explicit stakeholder sign-off.

## Related investigation inventory

Full file/route/test-level inventory with reference lines:
`docs/ADR-0002-impact-inventory.md`.