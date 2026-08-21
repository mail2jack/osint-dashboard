# ADR-0001 — Subject-First Investigations

- Status: **In Progress (pilot production)**
- Date: 2026-08-15
- Updated: 2026-08-21
- Deciders: Ivan Versteegh (owner), Perry Couprie (review), external advisor
  review (ChatGPT plan), codebase verification (OpenCode PR1 inventory)
- Related docs: `subject-model-inventory.md` (evidence),
  `subject-profile-contract.md` (target contract)

## Context

Investigations are currently case-bound. `research_actions` has no
`subject_id` and action runners resolve their target via
`action.case.subjects.first()` (`cms/workflow/actions/helpers.py:39-41`,
used by 9 action modules). Two independent input paths (standalone subject
CRUD and the workflow case screens) write the same table with divergent
logic, producing confirmed round-trip failures (10 documented in the
inventory, e.g. `registration_number`/`legal_form` dropped on edit, `name`
never recomputed on edit, `online` silently converted to `person`, social
handles split across two stores). Search over encrypted identifiers is
broken because ILIKE runs against ciphertext. Subject types are ambiguous
(`company` vs `organization`, ad-hoc `online`). Biometric and GPS metadata
(`face_encoding`, `photo_metadata`) are stored as plaintext JSON.

The product direction is a **subject-first** model: an entity profile is the
anchor for a case, with controlled facts, an investigation package, and a
candidate → validation → finding flow.

## Decision

Implement in 8 small PRs behind feature flag `subject_first_investigations`,
never as a big-bang replacement. This ADR fixes the design decisions; PRs
1..8 implement them incrementally.

### D1. Phasing (8 PRs, each with CI + explicit approval)

1. **Inventory + ADR/datamodel** (this PR): no functional change, no
   migration, no backfill. Delivers inventory, contract, ADR, and the
   read-only production audit script.
2. **SubjectService + round-trip fixes**: one central read/write service used
   by both input paths; create/edit/view round-trip safe; regression tests
   per type; no data-model change.
3. **Data model phase 1**: `subject_identifiers`, `subject_facts`; addresses,
   contacts, social accounts extended with source/status/timestamps/action
   link; `subject_relations` typed/directed; `case_subjects` role/status/note.
   Migration up/down.
4. **Actions subject-centric**: `subject_id` on `research_actions`
   (case_id stays mandatory for authorization + reporting); explicit target
   + input snapshot; presets per subject type; remove `_first_subject`.
5. **Research flow**: "propose investigation" screen; free/local/open actions
   ready as proposals; candidates stored; validation promotes to verified
   fact/finding; findings keep action, source URL, raw response, timestamp,
   integrity hash.
6. **Dork-first source policy**: local normalization, public registers,
   search links, dork templates as the default; composed browser query is a
   proposed action the investigator starts — **no silent browser automation
   and no bulk queries at subject creation**; paid channels off by default
   behind explicit tenant config + cost label; existing API connectors remain
   available as optional depth.
7. **UX**: single Subject Profile with tabs (Overview, Identity, Contact,
   Addresses, Financial, Online, Relations, Investigation, Facts, Findings);
   per-type intake forms; edit screens rendered from the DB read-model; show
   provenance, verification, last change per value.
8. **Rollout**: feature flag on; backfill existing findings/actions to source
   links where unambiguous; old screens read-only/fallback; pilot with real
   cases; only then remove the old workflow.

### D2. Two-truth problem: fact layer is the source of truth for new data

`subject_facts` / `subject_identifiers` are the single source of truth for
**new** data. Legacy `subjects` columns stay readable for compatibility but
get a fixed transition policy: any write path that targets a legacy column
must (a) write the fact layer, and (b) mirror to the legacy column only while
the feature flag is off. **No unlimited dual-write**: once the flag is on,
legacy columns are read-only and become plaintext-free mirrors if still
needed. Backfill of existing encrypted columns into the fact layer is a
separate, later phase with its own migration review.

### D3. Encrypted search via keyed fingerprints

For email, phone, IBAN, license plate, and identifiers, store a derived,
keyed, HMAC-signed fingerprint (deterministic on normalized input, keyed with
a separate fingerprint key, never the plaintext, never the encryption key) in
a companion column. Duplicate detection and search query only the
fingerprints. Never plaintext indexes; never ILIKE on ciphertext.

### D4. Case-privacy is leading

A subject may belong to multiple cases within a tenant, but access is always
scoped through the cases a user is authorized for. Facts and actions inherit
the case access rules (`ensure_case_access`); there is **no** tenant-wide
"subject admin" surface that bypasses case scope. PRs must include case-
isolation tests proving no cross-case leakage.

### D5. Duplicates and merge are a later, separate phase

The data model (typed/directed relations with source and confidence) is built
in PR3 to make detection and merge possible. Detection, review, and an
auditable merge flow for persons, accounts, emails, IBANs, plates, and
vehicles ship later. **Never auto-merge.**

### D6. Special-category data policy

BSN, travel documents, bank data, biometrics (`face_encoding`), and GPS
metadata (`photo_metadata`) are only stored encrypted, strictly authorized,
fully audited, and **excluded by default** from broad exports/reports unless
explicitly selected. `face_encoding`/`photo_metadata` encryption is
implemented in the data-model phases; the contract already requires masked
display by default.

### D7. Provenance and status are mandatory

Every fact carries source, action, timestamp, reliability, and status
(`candidate`, `verified`, `rejected`, `superseded`). Automatic output is never
promoted to verified without human validation.

## Target data model (PR3)

Keeps the `subjects` table name for compatibility. New/changed storage:

```text
subject_identifiers
  id, subject_id FK, tenant_id FK
  identifier_type   -- email|phone|iban|license_plate|bsn|document|platform_handle|...
  value_enc         -- Fernet-encrypted canonical value
  fingerprint_keyed -- HMAC fingerprint (D3)
  status            -- candidate|verified|rejected|superseded
  source, source_url, observed_at, reliability
  action_id FK, finding_id FK (nullable)
  created_at, updated_at, created_by

subject_facts
  id, subject_id FK, tenant_id FK
  fact_key, value_enc, status, source, source_url, observed_at
  reliability, verified_by, verified_at
  action_id FK, finding_id FK (nullable)
  created_at, updated_at, created_by

addresses        + source, status, observed_at, action_id, finding_id, updated_by
contacts         + source, status, observed_at, action_id, finding_id, updated_by
social_accounts  + source, status, observed_at, action_id, finding_id, updated_by

subject_relations
  subject_id, related_subject_id
  relation_type    -- family|business|other
  direction        -- outgoing|incoming|mutual (replaces double-row storage)
  source, reliability, status, observed_at, case_number (optional)
  created_at, created_by

case_subjects     + role_in_case, status, note

research_actions  + subject_id FK (nullable = explicit case-wide scope)
                  + target_kind, target_snapshot (normalized input at creation)
```

`subject_id` on `research_actions` is nullable only to express an explicit
**case-wide** scope; a non-null value must always be a subject linked to the
action's case.

## Consequences

- **Positive**: reproducible subject-targeted investigations; single input
  path; searchable encrypted data; auditable provenance; reportable facts.
- **Negative/risk**: legacy columns + fact layer coexist during transition
  (mitigated by D2); action-migration and backfill need careful mapping;
  fingerprint keys add key-management surface (rotation must be supported);
  the work is spread over 8 PRs and several phases before the full UX lands.
- **Migration guardrails**: every PR ships up/down migrations, tenant + case
  isolation tests, round-trip tests for changed fields, and a manual
  acceptance test list. No merge without explicit approval after green CI
  and review.

## Compliance checklist (per PR, from the original brief)

- [x] No action runs without an explicit subject or an explicit case-wide scope.
- [ ] No paid channel is used without an explicit opt-in.
- [x] Every edit is fully round-trip correct for the subject's field groups.
- [x] Every automatic result has source, action, timestamp, and status.
- [ ] Only validated facts reach formal reporting by default.
- [x] A user only sees subjects/facts/actions within accessible cases and tenant.
- [x] Migration up/down plan, isolation tests, round-trip tests, manual
      acceptance list, and explicit approval accompany every PR.

## Implementation status (2026-08-21)

| PR | Fase | Status | Commits |
|---|---|---|---|
| PR1 | Inventory + ADR | ✅ Gemerged | `8be6edd` |
| PR2 | SubjectService + round-trip fixes | ✅ Gemerged | `16f9332` |
| PR3 | Data model phase 1 | ✅ Gemerged | `f0a1b2c3d4e5` |
| PR4 | Actions subject-centric | ✅ Gemerged | `f4e5d6c7b8a9` |
| PR5 | Research flow | ✅ Gemerged | `b8c9d0e1f2a3` |
| PR6 | Dork-first source policy | ✅ Gemerged | `d0e1f2a3b4c5` |
| PR7 | UX (Subject Profile) | ✅ Gemerged | `a1b2c3d4e5f7` |
| PR8 | Rollout | ✅ In pilot | `178ed19` |

### Aanvullende implementaties (na PR8)

| Omschrijving | Status |
|---|---|
| CSP nonce fix (3 inline scripts) | ✅ `4d60dd1` |
| Browser smoke tests (requests.Session) | ✅ `dc9020e` |
| Security review #55, #56, #58 | ✅ `766b0ae` |
| Key rotation (multi-key fallback) | ✅ `abbb2fe` |
| Encryption diagnostics & recovery | ✅ `b865f82` |
| Search scalability (SQL ILIKE) | ✅ `178ed19` |
| Composite indexes (tenant isolation) | ✅ `c3d4e5f6a7b8` |

### Pilotstatus

- Feature flag: `subject_first_investigations_global = 1`
- Per-tenant: enabled voor Default Organization + Neonova Nederland
- TOTP secret: geroteerd 21-08-2026
- Productie health: alle services OK
- Volledige CI: 736/736 tests passed
