# Design (PR4): Start Research Actions from the Investigation Workspace

**Status:** Implemented — draft PR awaiting independent review
**Author:** mail2jack / opencode
**Date:** 2026-09-11 (minimal modal design decided per user)
**Depends on:** PR3 (merged, `b0571d4`) — Investigation Workspace detail page, subjects/actions/findings display, timeline, link/unlink, audit trail
**Feature flag:** `investigation_workspace` — stays default OFF

> **Revision note (2026-09-11):** the original "reuse the case-detail picker" design was
> abandoned after code inspection: there is **no** `_action_form.html`, and the existing
> picker (`_workflow_picker.html` + `_workflow_polling.html` + `_workflow_events.html`) is
> deeply coupled to the case-detail DOM (action-grid `[data-action-key]` buttons,
> `#actionsStatus`, `#findingsContainer`, `#proposalsSection`, plus globals from
> `_workflow_js_config.html`: `CASE_ID`, `CAN_WRITE`, `SUBJECTS`, `INVESTIGATIONS`).
> The workspace therefore gets a **dedicated, self-contained "Start Action" modal** instead.

---

## 1. Problem Statement

Investigators must leave the Investigation Workspace to start research actions from the
main case detail page, then link the action back to the investigation. Scope is not
explicitly controlled at creation time.

**PR4 objective:** start research actions directly from the workspace, with the current
investigation pre-selected as explicit scope (visible to the user), an explicit
"case-wide" option, and full server-side validation. No implicit relinking. Both
proposal and direct-run modes. Full audit trail. Archived investigations blocked.

---

## 2. What Already Exists (PR1/PR2/PR3 Foundation)

| Component | Location | Status |
|---|---|---|
| `run-action` POST API | `cms/workflow/routes.py:1469` | ✅ Complete — accepts `investigation_id`, `subject_id`, `mode` |
| `get_linkable_investigation()` | `cms/services/action_scope.py:17` | ✅ Validates exists / same case / same tenant / OPEN / not archived |
| `require_open()` | `cms/services/investigation_service.py:47` | ✅ Blocks archived/closed investigations |
| `log_scope_audit()` | `cms/services/action_scope.py:55` | ✅ Same-transaction audit per create + scope change |
| `ACTION_REGISTRY` | `cms/workflow/actions/registry.py` | ✅ All action types, icons, labels, categories |
| `_investigator_required` | `cms/workflow/routes.py:131` | ✅ Role gate (investigator/senior_investigator/admin/owner) |
| Proposal + direct-run mode | `cms/workflow/routes.py:1524-1607` | ✅ `mode="proposal"` and `mode="run"` both supported |
| Scope link API | `cms/workflow/routes.py:1610` | ✅ Existing link/unlink endpoint (audit-logged) |
| Workspace detail template | `templates/cms/workflow/workflow_investigation_detail.html` | 🔧 Needs: "Start Action" button + minimal modal |
| Workspace route context | `cms/workflow/routes.py:1144` | 🔧 Needs: action types + subjects for the modal |

**The core gap is UI wiring + a small context addition.** All backend validation, audit,
and scope management already work and are covered by tests in
`tests/test_research_action_link_api.py` (scoped run, case-wide default, cross-case/
cross-tenant/archived rejects, proposal scope, no-implicit-relink).

---

## 3. Design: Minimal "Start Action" Modal (decided 2026-09-11)

A compact modal in the workspace template, posting to the **existing** `run-action`
endpoint. Rich pickers (dork library, photo analysis) intentionally stay on the case
page — out of scope for PR4.

### Modal fields

| Field | Control | Notes |
|---|---|---|
| Action type | `<select>` | From `ACTION_REGISTRY` (label + icon); skips `photo_analysis` (needs file upload UI) and `manual_entry` (needs rich form). Paid types (`facebook`, `instagram`, `tiktok`, `linkedin`, `twitter`) are rendered `disabled` until the per-tenant `paid_channels` flag is on; the server-side 409 remains the authoritative guard. |
| Scope | `<select>` | Default = **current investigation** (human_number); second option `🌐 Case-wide (no investigation)` → posts `investigation_id: null`. This is the explicit-scope requirement. |
| Subject | `<select>` | All case subjects (decrypted display_name, DTO dicts only); empty option = case-wide target (`subject_id: null`) |
| Data value | `<input>` | Label/hint switches per action type |
| Mode | radio | `Run now` / `Save as proposal` |

POST body:

```json
{ "action_type": "...", "data_value": "...", "subject_id": "...|null",
  "investigation_id": "...|null", "mode": "run|proposal" }
```

Response `{id, status}` on success → toast + `location.reload()` so the action list /
timeline / counts update. `{error}` → `showToast(error, 'error')`.

### Security / robustness properties

- `subjects_cfg` is a list of plain DTO dicts (`{id, display_name, subject_type}`);
  ORM objects are never passed to the modal. The rows come from an **explicit,
  case-scoped query on the case-subject junction** that also filters
  `Subject.tenant_id == case.tenant_id` and `Subject.is_deleted.is_(False)`
  (review P1-1), and are sorted deterministically by display name
  (case-insensitive) then id. Only plaintext name fields are rendered, so
  `decrypt_identifiers()` is **never** called on this path: no cipher is
  touched, nothing is re-encrypted, and no autoflush side-effect can occur.
- **`can_start_actions` (review P1-2)** is computed server-side as
  `can_write and inv.status == InvestigationStatus.OPEN.value and
  inv.archived_at is None` — the same positive invariant as
  `get_linkable_investigation()` / `require_open()`. It alone gates the
  Start Action button, the modal markup and the modal JS; no divergent
  template-side status checks exist. The workspace stays *readable* for
  archived/closed/inconsistent investigations, but `get_linkable_investigation()`
  remains the authoritative server-side security control on every POST.
- Modal JS uses `window.apiFetch` (CSRF-safe, base.js) and interpolates `{{ case.id }}`
  into the URL — no dependency on the case-detail global `CASE_ID`.
- Toast/status text is assigned via `textContent`; option values are Jinja-escaped.
- A `_submitting` guard plus a disabled submit button prevent duplicate actions on
  double-click; the submit button is re-enabled in `finally`.
- Button and modal (and their JS) render only when `can_start_actions` is true
  (writer role, status OPEN, no archive timestamp).
- The original `data-confirm-archive` handler remains its own single-load nonce
  script with non-overlapping selectors, so no listener is ever registered twice and
  the CSP nonce tests stay green.
- Accessible dialog (`role="dialog"`, `aria-modal`, `aria-labelledby`): focus moves
  to the first field on open, is trapped via the Tab handler, Escape closes the modal,
  clicking the overlay closes it, and focus returns to the trigger button on close.

---

## 4. Implementation

### Backend context (`cms/workflow/routes.py`, `investigation_detail`)

- `can_start_actions`: `can_write and status == OPEN and archived_at is None`
  (positive invariant, identical to the server validators). Passed as a single
  boolean; the template renders button + modal + JS only under it.
- `action_types`: list of `{key, label, icon, category}` for `ACTION_REGISTRY`,
  excluding `photo_analysis` and `manual_entry`.
- `subjects_cfg`: list of `{id, display_name, subject_type}` built from an explicit
  `case.subjects.filter(tenant==case, is_deleted=False)` query (no
  `decrypt_identifiers()`), sorted deterministically by display name
  (case-insensitive) then id.
- `paid_enabled`: `paid_channels_enabled()` — lets the template mark paid types.

No changes to `build_inv_workspace`, `run_action`, or validation. The workspace
query-count bound measures `build_inv_workspace` only, so the template-context
lookups do not affect it.

### Template (`workflow_investigation_detail.html`)

- **Button:** in the `{% if can_write %}` action row, when `not inv.archived_at`:
  `<button type="button" class="btn-cms small" data-open-start-action>`.
- **Modal:** self-contained overlay, local CSS in the existing style block, no
  dependency on the case-detail picker CSS.
- **JS (own nonce script inside the same `{% if %}` block):** open/close on
  `data-open-start-action` / `data-close-start-action`; on submit build the JSON and
  POST to `/cms/workflow/api/case/{{ case.id }}/run-action`; on `json.error` →
  `showToast(err, 'error')`; else toast + reload.
- `photo_analysis`/`manual_entry` are not offered in the dropdown, so the
  "cannot be proposed" guard does not apply here.

### Data value hints (JS, per action type)

The data-value label + placeholder switch by action type, e.g. `google_dork` →
"Dork query" / `site:domein.nl`, `browser_search` → "Search query", otherwise free
text. Purely presentational; server does the real validation.

---

## 5. Files Changed

| File | Change |
|---|---|
| `cms/workflow/routes.py` | `investigation_detail`: add `can_start_actions`, `action_types`, `subjects_cfg` (explicit tenant/case/soft-delete scoped, non-decrypting), `paid_enabled` to context |
| `templates/cms/workflow/workflow_investigation_detail.html` | Start Action button + modal + JS (+ restored archive-confirm nonce script; accessible dialog) |
| `tests/test_investigation_workspace.py` | `TestWorkspaceStartAction` (22 cases: visibility status matrix, scope default, subjects filter matrix, action-type offering, paid shielding, ciphertext-unchanged, end-to-end scoped + audited run, POST-rejection matrix) |
| `translations/{nl,en}/LC_MESSAGES/messages.{po,mo}` | New modal msgids |
| `docs/design-investigation-workspace-pr4.md` | This document |

**No changes** to: `run_action`, validation services, `_workflow_picker.html` /
`_workflow_polling.html` / `_workflow_events.html`, `build_inv_workspace`, or the
case-detail picker infrastructure. No migration or schema change.

---

## 6. Non-Goals (Out of Scope)

- Reusing the case-detail picker/dork-library/photo-upload UI on the workspace page
- Pre-filling the picker by clicking a workspace subject card (future UX enhancement)
- Bulk action creation, templates, presets, quotas
- Moving/archiving actions from the workspace (existing link/unlink UI on case detail remains canonical)

---

## 7. Risk Assessment

| Risk | Mitigation |
|---|---|
| Wrong investigation scope | Scope select defaults to current investigation and is visible; "case-wide" must be chosen explicitly; server re-validates on every POST via `get_linkable_investigation()` |
| Archived investigation used as scope | Button hidden when archived; server refuses with 400 |
| Viewer starts an action | Button hidden when `can_write=False`; `_investigator_required` on `run-action` |
| Paid channel misuse | Modal marks paid types when `paid_enabled=False`; server rejects with 409 |
| Duplicate actions by double-click | `_submitting` guard + disabled submit button, re-enabled on completion |
| XSS via modal data / toasts | Jinja escaping for options, `textContent` toasts, JSON body / escaped re-render |
| Modal breaks workspace query-count bound | New lookups are on the route, not in `build_inv_workspace`; bound unchanged |

---

## 8. Rollout

1. Implement → unit tests + lint + typecheck
2. Draft PR to `master` with all changes
3. Independent review (separate agent): template, JS, server flow, test coverage
4. Merge when approved → post-merge CI → deploy
5. **Feature flag stays OFF** — activation only after PR4 review is complete
6. Broad activation deferred (user decision point)