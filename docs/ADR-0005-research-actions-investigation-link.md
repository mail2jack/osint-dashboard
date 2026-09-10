# ADR-0005 — ResearchAction ↔ Investigation link (next phase, post PR2)

- Status: **Proposed (draft for approval — precedes the data/migration PR)**
- Date: 2026-09-08
- Deciders: Ivan Versteegh (owner), codebase verification (OpenCode)
- Related docs: `ADR-0002-investigations-within-case.md` (phasing, PR 4),
  `ADR-0002-impact-inventory.md` (evidence)

> **Context of this PR-phase:** PR2 of the current UX round is a strictly
> UI-only interim step ("Zaak als werkruimte"). It deliberately does **not**
> suggest any action↔investigation coupling because the relation does not
> exist yet. This ADR defines the design for the **subsequent** data/migration
> PR that introduces the real, persistent link. Only after this ADR is
> approved and that data PR lands may a later UI PR group actions, findings
> and filters per investigation.

## Context

- `ResearchAction` today is linked to `Case` (+ optional `Subject`) and has
  **no** relation to `Investigation`
  (`cms/models/__init__.py:1948`).
- `Investigation` is a container inside a `Case` with a hard tenant invariant
  (`investigation.tenant_id == investigation.case.tenant_id`), composite FK
  to `cases(id, tenant_id)` and immutable identity
  (`ADR-0002` D1/D3/D8; migration `dd1e2f3a4b5c7`).
- `Findings` may link to actions of multiple investigations via
  `ActionFinding` (`ADR-0002` D6): a finding is **not** bound to one
  investigation.
- The existing case-wide actions (no `Subject`, `target_kind='case'`) are the
  baseline to preserve: their semantics must remain explicit.

## Decision

### D1. Persistent nullable link

Add a nullable column `research_actions.investigation_id` that references
`investigations(id)`:

- `NOT NULL` → the action is **bound to that investigation** (new, scoped
  work).
- `NULL` → the action is **case-wide by explicit semantics** (existing
  behavior, unchanged).
- Existing rows stay `NULL`; no migration rewrites them (see D4).

### D2. Same-case / same-tenant DB invariant

`research_actions.investigation_id` must never point to an investigation of a
different case or tenant. Enforce at database level, mirroring
`ADR-0002` D8. A FK on `(investigation_id, tenant_id)` is **not**
sufficient: it only proves the investigation exists and that both rows share a
tenant — it does **not** tie the action to the same *case*. Two compliant
options:

- **Option A (composite FK on a parent key):** a FK on
  `research_actions(investigation_id, case_id, tenant_id)` referencing a
  unique parent key `investigations(id, case_id, tenant_id)`. The composite
  FK hard-locks the same-case relation at the schema level: every
  `case_id`/`tenant_id` written must match the referenced investigation's own
  case and tenant.
- **Option B (DB trigger):** a trigger on `research_actions` (BEFORE
  INSERT/UPDATE) that rejects any row where the referenced investigation's
  `case_id`/`tenant_id` differ from the action's `case_id`/`tenant_id`.
  A standalone FK on `(investigation_id)` alone is insufficient with this
  option for the same reason as above.

Requirement: the invariant must hold **even when RLS is bypassed**
(`app.bypass_rls`), like `ADR-0002` D8 — prove with a bypass-path test.
Service-side validation mirrors the DB check; writes only within the tenant
boundary.

### D3. Authorization & audit

- Access to any linked investigation inherits **case access** (`ADR-0002`
  D7); there is no standalone investigation access surface.
- Audittrail: setting/claring `investigation_id` on an action is logged via
  `AuditLog` (`entity_type="research_action"`) with the actor, IP and both
  values; default writes (`NULL`, case-wide) are also logged so the scope is
  auditable end-to-end.

### D4. Backfill policy

- **No** backfill of historical case-wide actions into investigations.
  Existing rows remain `NULL` (= case-wide, D1) indefinitely.
- If an investigation is later identified for an action, that is a **new
  deliberate link** (an audit-logged write), never an automated migration.
- Gaps/immutability of investigations (`ADR-0002` D3) are untouched.

### D5. Explicit case-wide semantics

- `investigation_id = NULL` on `research_actions` is the single, documented
  meaning of "case-wide action"; the UI labels such actions "Case-breed"
  (PR2) and a later UI PR may filter/group on exactly this predicate.
- Manual case-wide findings stay case-wide (`ADR-0002` D6, option a); the
  `investigation_id` column must **not** be added to `findings` in this data
  PR.

## Consequences

- PR2 (current, UI-only) is unaffected and honest: no pseudo-coupling.
- The data/migration PR (next) adds the column + invariant + audit + tests,
  keeping case-wide actions valid and unchanged.
- A subsequent UI PR can then group action cards, proposals and findings by
  investigation and filter on `investigation_id IS NULL` for the case-wide
  bucket — without inventing semantics.

## Migration notes for the data PR (not part of PR2)

- Add nullable `research_actions.investigation_id`; if the composite-FK route
  (D2 Option A) is chosen, add the unique parent key
  `investigations(id, case_id, tenant_id)` and the matching composite FK on
  `research_actions(investigation_id, case_id, tenant_id)` — or implement the
  D2 Option B trigger instead. Plus FORCE RLS re-check and rollback migration.
- Tests: JSON-type must build in `test_postgres_integration.py`,
  migration up/down in `test_migration_cycle.py`, invariant tests for the
  normal and bypass-RLS paths, RLS/authorization tests, audit log entries.

## Addendum 1 — Implementation status (PR-A merged, PR-B drafted)

### D6. Link targets are open, non-archived investigations

- An action may only be linked to an investigation that is **open**
  (`status = "open"` **and** `archived_at IS NULL`). Archived investigations
  are refused at the service layer with a clean 400 (the composite FK cannot
  express this rule).
- **Archiving never unlinks**: historical links on already-linked actions are
  kept; only a deliberate link/unlink write changes scope (D4 stays intact).

### D7. Link/unlink endpoint contract

- `POST /api/case/<case_id>/actions/<action_id>/link` with
  `{"investigation_id": "<id>"}` and `DELETE …/link` (back to case-wide):
  both must confirm that the **action** and the **investigation** belong to
  the route `case_id` and tenant, otherwise 404 (missing/mismatched action or
  investigation) / 400 (wrong case, wrong tenant, archived). Idempotent
  re-links produce no new audit entry.

### D8. Create-path audit (incl. pre-existing gap)

- `run_action` historically created actions **without** an `AuditLog` entry;
  all create paths now log the explicit scope (`investigation <id>` or
  `case-wide`) with old/new values — scope is auditable end-to-end.
- **Create + scope-audit is one transaction** (add → flush → audit → commit):
  an action can never exist without its audit record; a failed audit write
  rolls the action create back.
- Bulk proposals write **one AuditLog entry per created action** (each with
  its own `entity_id` and old/new `investigation_id`) in a single atomic
  commit.
- `investigation_id` is forwarded from the request body on the user-driven
  create paths (`run_action`, `create_proposals`, subject-profile run-action);
  system/synthetic creates (e.g. `manual_entry`) always stay NULL.

### Execution notes

- **PR-A** (data PR, PR #146, merged): nullable `investigation_id` +
  composite FK `fk_research_actions_investigation_case_tenant` →
  `uq_investigations_id_case_tenant` (migration `f5a6b7c8d9e0`). PostgreSQL
  violations surface as **23503 (`foreign_key_violation`)**, not 23514 —
  verified by bypass-path tests in `test_postgres_integration.py`. SQLite
  keeps the parent key as a unique index (no table rebuild, immutability
  triggers survive). No backfill; NULL stays case-wide. Deploy per
  `docs/deploy-plan-adr0005-pr-a-schema.md`.
- **PR-B** (service/API, pending): `cms/services/action_scope.py` +
  create-path wiring + link/unlink endpoints + API tests.
- FORCE RLS on `research_actions` — handled in **Addendum 3 / PR #154**
  (migration `f6a7b8c9d0e1`, CLI fix, full PG test coverage).

## Addendum 2 — PR-C UI (case detail + subject profile)

### D9. UI scope model

- Every research action in the case workflow (findings groups) and the
  subject-profile action table shows a **scope badge**: `🌐 Zaakbreed` when
  `investigation_id IS NULL`, otherwise `🔗 <human_number>` (title shown as
  tooltip). Archived investigations stay resolvable so historical links keep
  their badge (D4/D6).
- The Step 5 findings header adds a **scope filter** (`Alle scopes /
  Zaakbreed / <investigation>`). Filtering is client-side; an empty result
  shows a dedicated no-match message instead of a blank panel.
- New actions in the case workflow (picker runs incl. dork variables,
  proposals, edit-confirm) and the subject-profile forms (**Propose action**
  and **Quick Start**) expose an optional **Research picker** that defaults to
  "Zaakbreed". Only **open** investigations of exactly the same case are
  selectable; the server re-validates via the PR-B service (D6/D7).
- Existing actions can be re-scoped from the findings-group header via a
  link/unlink `<select>` that calls `POST / DELETE …/actions/<id>/link`
  (idempotent, audited — D7). Selecting the current value is a no-op; the
  select is rebuilt from the `case_status` polling payload.
- The scope `<select>` is **writer-only**: it renders only when `can_write` is
  true (CaseDetail is behind `_investigator_required` anyway), and the polling
  JS skips drawing it whenever `CAN_WRITE` is false. Viewers keep the read-only
  badge. The API itself validates every change (PR-B), so this is pure UX.
- Picker `<option>`s (case workflow + subject profile) are built with the DOM
  API (`document.createElement('option')` + `textContent`), never `innerHTML`:
  the user-entered investigation title can therefore only render as text, and
  is additionally shipped to the browser as `tojson` (escaped) data.

### D10. Non-goals

- `photo_analysis` keeps its own upload endpoint and **no**
  `investigation_id`, so photo runs stay case-wide in PR-C. Extending the
  file-upload path with scoping is a possible later addendum, deliberately out
  of scope here.
- No data-model, RLS or migration changes. The UI only reads
  `investigations_meta` (case detail) / `investigation_options` (profile) and
  posts bodies the PR-B API already accepted; `archived` investigations appear
  in lookup data purely to keep historical badges unfiltered.

### Execution notes

- **PR-C** (UI, this change): read-only serialization (`case_status` action
  payload, `case_detail.investigations_meta`, `profile_view` action rows,
  `subject_profile.investigation_options`) plus templates
  (`_workflow_js_config`, `_workflow_picker`, `_workflow_polling`,
  `_workflow_events`, `workflow_case_detail`, `subjects/profile`) and
  `tests/test_research_action_link_ui.py`.

## Addendum 3 — RLS closure (security gap)

### D11. Context

Every other tenant-scoped table carries FORCE RLS (see the pinned matrix in
`tests/test_postgres_rls_matrix.py`, `EXPECTED_FORCE_RLS_TABLES`), so a
non-superuser connection can only reach rows whose ``tenant_id`` matches the
``app.tenant_id`` session GUC (or runs under the explicit ``app.bypass_rls``
flag). The ADR-0005 tables were the exception:

- ``research_actions`` has its own NOT NULL ``tenant_id`` but was never added
  to FORCE RLS — any request/worker/CLI path could list or write job data
  across tenants.
- ``action_findings`` is a junction table without any tenant column at all.

### D12. Decision — FORCE RLS for `research_actions` and `action_findings`

Migration ``f6a7b8c9d0e1`` closes the gap (PostgreSQL only, mirroring the
surrounding RLS migrations):

- ``research_actions``: ENABLE + FORCE RLS with the standard
  ``tenant_isolation`` policy on its direct ``tenant_id`` column
  (``USING``/``WITH CHECK`` same as every other tenant table).
- ``action_findings``: ENABLE + FORCE RLS with a ``tenant_isolation`` policy
  whose ``USING``/``WITH CHECK`` resolves the tenant *through the owning
  ``research_actions`` row* (`EXISTS (SELECT 1 FROM research_actions ra
  WHERE ra.id = action_findings.action_id AND ra.tenant_id = <ctx>)`). No
  schema change: the NOT NULL FK on ``research_actions.id`` guarantees a
  junction row always has a parent, so the subquery is fail-closed and
  index-accelerated (action_id is the PRIMARY KEY). This keeps the app code
  untouched — no ``tenant_id`` column, backfill, or model churn.

``investigations`` already had FORCE RLS since ``bb1c2d3e4f5a7`` and
``findings`` (the ``WorkflowFinding`` alias) since ``d2e3f4a5b6c7``; they are
part of the same isolation boundary but needed no change.

### D13. Context audit (request / worker / CLI)

- **Request**: ``app.py`` ``before_request`` (``set_tenant_context``) sets the
  GUC for every request; ``ResearchAction.tenant_id`` inserts are auto-filled
  from ``flask.g.tenant_id`` (``_TENANT_MODELS``) which always equals the GUC,
  so ``WITH CHECK`` passes and reads stay tenant-scoped.
- **Worker / async**: ``run_action`` (``run_action(action_id)``) looks the row
  up under a temporary bypass, scopes the whole run to the action's tenant,
  re-asserts the context around commits, and resets the connection in
  ``finally`` so nothing leaks to the pool.
- **CLI**: ``scripts/backfill_subject_actions.py`` is an explicit
  cross-tenant maintenance tool; it now re-establishes
  ``set_tenant_context(db, None, bypass_rls=True)`` before scanning. Without
  it, FORCE RLS would hide every row and the tool would silently scan zero
  actions. ``scripts/seed_testdata.py`` already sets an explicit bypass.

### D14. Tests

All PostgreSQL coverage runs under a real non-superuser role
(``NOSUPERUSER NOBYPASSRLS``) in CI and locally, so RLS is genuinely
enforced (a superuser would bypass everything):

- ``test_migrations_reach_head_and_enable_forced_rls`` now pins the new head
  and asserts ``research_actions``/``action_findings`` are FORCE + ENABLE.
- Matrix: ``research_actions``/``action_findings`` pinned in
  ``EXPECTED_FORCE_RLS_TABLES``; cross-tenant isolation tests (row visible as
  its own tenant, hidden as any other tenant and hidden without any context).
- ``WITH CHECK`` enforcement: inserting a research way-action with a foreign
  ``tenant_id`` (or a junction row for another tenant's action) is rejected
  with SQLSTATE ``42501`` — no bypass.
- CLI regression: running ``backfill_subject_actions.py`` end-to-end as a
  subprocess under FORCE RLS still scans the seeded rows.
- The cold-worker integration test now hands ``run_action`` a plain action id
  string (exactly what the real poller passes) instead of re-reading an
  expired ORM instance across a context wipe, which the new FORCE RLS
  correctly makes invisible pre-hand-off.

### Execution notes

- **PR (this change)**: migration ``f6a7b8c9d0e1``, CLI fix, PG test
  extensions. Deployment follows the normal controlled flow
  (``update.sh`` → head check via ``osint-health-refresh``); the migration is
  DDL-free on SQLite and only ``ALTER TABLE … ROW LEVEL SECURITY`` statements
  on PostgreSQL (reversible via ``downgrade()``).
- **Functional pilot before production-standard rollout**: a disposable case
  is used to verify one case-wide action and one investigation-linked action,
  the scope badges/filter/profile display, link/unlink on an existing action,
  and the audit trail — then the case is cleaned up.
- RLS on `research_actions` and `action_findings` is **closed** by Addendum 3
  / PR #154 (migration `f6a7b8c9d0e1` + CLI fix + full PG test coverage).