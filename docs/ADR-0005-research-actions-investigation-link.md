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