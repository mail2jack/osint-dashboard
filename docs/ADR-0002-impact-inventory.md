# Impact inventory — ADR-0002 Investigations within a Case

Evidence for `ADR-0002-investigations-within-case.md`. Status:
**proposed (PR 1, review-only)**. Line references are to `master @ 5ba9d43`.
No functional change is made by this PR.

## 1. Case creation & number generation

| Impact | Evidence |
|--------|----------|
| `generate_case_number()` uses a `MAX()+1`-style scan (`case_number.like(f"{year}-%")`, integer parse + increment). This is exactly the pattern **forbidden** for PR 2. | `cms/models/__init__.py:716` |
| Uniqueness today is DB-enforced per tenant. | `uq_tenant_case_number` — `cms/models/__init__.py:630` |
| `case_number_prefix` general setting is stored but **unused** — candidate for the issuance config, to be decided in PR 2. | model ~`cms/models/__init__.py:3026` |
| Workflow create lets the user **override the generated number** via `raw_number`; template pre-fills it. This conflicts with "immutable human number" (D3/D4). | `cms/workflow/routes.py:807-812`; `templates/cms/workflow/case_new.html:~61` |
| Workflow edit writes the submitted `case_number` directly, no validation/audit. Legacy CRUD edit does not allow a number change. | `cms/workflow/routes.py:1046`; `cms/routes/cases_crud.py` |
| **No tests** assert `generate_case_number()` format/uniqueness — PR 2 must add them (SQLite + PostgreSQL). | `tests/` |

**PR 2 actions:** introduce atomic issuance (never `MAX()+1`), add
`(tenant_id, case_id, sequence_no)` unique constraint, add immutability +
concurrency tests, decide the `raw_number` override question.

## 2. Workflow case detail, proposals & actions

| Impact | Evidence |
|--------|----------|
| Case detail is the central screen; actions are queried `filter_by(case_id=...)`; findings links loaded in the same view. Adding an investigation layer is additive here. | `cms/workflow/routes.py:848-980` (active case actions ~848-851; findings links ~878-889) |
| `ResearchAction` has UUID + label, **no sequence column** — the "Onderzoeksactie" numbering does not exist and is not required by the ADR. | `cms/models/__init__.py:1936` |
| Action execution: sync `run_action` + async thread dispatch; presets/`register.py`; 9 modules resolve target via `case.subjects.first()`. Investigation is orthogonal (PR 4 adds a nullable `investigation_id`, leaving case_id mandatory for auth/reporting). | `cms/workflow/actions/registry.py:172-355, 358-380`; `cms/workflow/actions/helpers.py:39-41` |
| Picker template with `actionType` branches is the reuse target for the investigation picker (like the RDW branch). | `templates/cms/workflow/_workflow_picker.html` (~line 220 RDW) |
| "propose investigation" flows exist at the **case** level today; the new screen (PR 3) is a sibling of case-detail, not a rewrite. | `cms/workflow/routes.py` (`case_new`, proposals routes) |

## 3. Subject Profile & Activity tab

| Impact | Evidence |
|--------|----------|
| Subject profile is case-scoped already (`accessible_case_ids`, case isolation); investigations inherit case access through the same gate — no new authorization surface (D7). | `cms/routes/subjects_list.py:316-399` (scope gate 334-354) |
| Profile "Investigation" tab labels the **case** today. Rename sweep to "Zaak" needed so "Onderzoek" is free for the new model. | `templates/cms/subjects/profile.html` |

## 4. Findings Register, reports / PV / PDF, exports

| Impact | Evidence |
|--------|----------|
| Findings are case-scoped in the register; include/exclude via `include_in_report` (ADR-0001 semantics). Additive investigation filter only in PR 5 (D5 default: case-wide, unchanged). | `cms/workflow/routes.py:394-505` (findings_index), `findings_api` ~508, `create_manual_finding` 1420-1463; `cms/models/__init__.py:1662` |
| A finding can link to actions of **multiple** investigations via the `ActionFinding` junction — do **not** add a NOT NULL `investigation_id` to findings (D6). | `cms/models/__init__.py:2033` |
| PV page heading is "Investigation report" (case-level text); PDF/report rendering and exports are case-scoped. | `cms/*` report templates; exports; `cms/routes/cases_reports.py` ~63/553 |

## 5. Invoicing

| Impact | Evidence |
|--------|----------|
| `Invoice.case_id` is the only case link today — no investigation dimension; investigation-level invoicing is out of scope until a later decision. | `cms/models/billing.py:27` |
| Invoice line labels use "Onderzoek: {case.title}" / "Zoekactie {label}: {case_number}" / "Proces verbaal: {case_number}" — the "Onderzoek:" label is case-level and wording should be revisited to avoid ambiguity with the new model. | `cms/services/invoice_service.py:119, 137, 159, 172` |
| Auto-invoice triggers on case-created/action-completed/PV-created stay case-wide. | `cms/services/invoice_service.py` |

## 6. APIs, deep-links & search results

| Impact | Evidence |
|--------|----------|
| Case detail/case-new are reached by UUID deep-links (`/cms/cases/<uuid>` …); investigation must get stable routes **derived from the case UUID + sequence_no**, not from mutable case_number. | routing in `cms/workflow/routes.py` / blueprints |
| Search results and list pages reference `case_number` and the "Investigation" heading at dashboard level — update display strings, keep stored data unchanged. | dashboard / search templates |
| No public API compatibility issue: internal routes only; keep old route patterns working. | standalone feature-flag rollout (PR 6) |

## 7. Existing parent/child-case functionality

| Impact | Evidence |
|--------|----------|
| `parent_case_id` / `child_cases` is the **existing** case hierarchy and stays untouched: not reused, not auto-migrated, not consulted by investigation queries. | `cms/models/__init__.py:~668-707` |
| The new `investigations.case_id` is a plain FK to `cases` — it must not interfere with the hierarchy FK. | model design, PR 2 |

## 8. Background workers, imports & migrations

| Impact | Evidence |
|--------|----------|
| Async action execution is in-process threading (no separate DB worker); adding an investigation filter does not require new worker infra. | `cms/workflow/actions/registry.py` |
| Import scripts and any data-in scripts must skip/ignore `investigations` until PR 6 backfill. | `scripts/`/import modules |
| Alembic is a single linear chain; new migration must be head and update the head literal test. | migration head `aa1b2c3d4e5f6`; `tests/test_postgres_integration.py:46` |
| RLS is FORCE-enabled on the protected set (cases, clients, notifications, subject_identifiers, subject_facts, …). `investigations` must follow the same pattern with `tenant_id` + `app.bypass_rls` context; actions check in the PG integration suite. | migration `f0a1b2c3d4e5f6`; `tests/test_postgres_integration.py:48-57` |

## Cross-cutting notes

- **Naming/UI sweep:** the workflow UI uses "Investigation" for a `Case`
  (dashboard heading, action labels, PV heading, profile tab, invoice labels).
  Freeing the term happens in small PR steps (3+).
- **Test matrix for PR 2 (new, must pass on SQLite **and** PostgreSQL):**
  atomic issuance under concurrency; unique `(tenant_id, case_id, sequence_no)`
  violation; sequence gaps preserved; case-number immutability; RLS tenant
  isolation + FORCE RLS; migration up/down + rollback; head-literal alignment.