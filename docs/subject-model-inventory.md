# Subject Data-Model Inventory & Round-Trip Audit

Status: living document produced by the subject-first redesign PR1 (inventory
phase). No functional change was made in PR1; this records the current state
so the redesign has a verified baseline.

Sources: `cms/models/__init__.py`, `cms/routes/subjects_*.py`,
`cms/workflow/routes.py`, `templates/cms/subjects/*`, `templates/cms/workflow/*`.
All line numbers refer to `master` at the time of writing.

## 1. Data model summary

| Table | Purpose | Encryption |
|---|---|---|
| `subjects` | Entity under investigation (person/company/organization/vehicle/vessel/online) | Manual Fernet on 22 fields |
| `addresses` | Structured addresses (0..n per subject) | Encrypted (5 fields) |
| `contacts` | Email/phone contacts (0..n per subject) | Encrypted (`value`) |
| `social_accounts` | Social platform accounts (0..n per subject) | Plaintext |
| `financial_records` | Transactions/verification records | Counterparty fields encrypted |
| `findings` | Verified/unverified findings, case-scoped, optional `subject_id` | None (plaintext) |
| `research_actions` | Case-scoped actions; **no `subject_id`** | None |
| `case_subjects` | Bare M:N join `case_id`+`subject_id` | – |
| `subject_relations` | Bare self-M:N `subject_id`+`related_subject_id`+nullable `relationship_type` | – |

Encryption mechanism: manual Fernet symmetric encryption (AES-128-CBC + HMAC)
via `cms/encryption_utils.py`. Encrypted columns are plain `db.String(500)`
holding a Fernet token; helpers `encrypt_identifiers()`/`decrypt_identifiers()`
handle them per object. The `EncryptedString` TypeDecorator exists but is
**unused** by models.

## 2. `subjects` columns by category

`Enc` = in `ENCRYPTED_FIELDS` (models/__init__.py:1062-1085).

### Identity (person)
| Column | Type | Enc | Notes |
|---|---|---|---|
| `achternaam` | String(200) | no | split-name field |
| `voornamen` | String(300) | no | split-name field |
| `voorletters` | String(20) | no | split-name field |
| `tussenvoegsels` | String(50) | no | split-name field |
| `name` | String(300), NOT NULL, indexed | no | computed full name; legacy fallback |
| `geslacht` | String(20) | no | man/vrouw/onbekend/geen_opgave |
| `subject_type` | String(20), NOT NULL, indexed | no | person/company/organization/vehicle/vessel/online |
| `date_of_birth` | String(500) | **yes** | |
| `place_of_birth` | String(500) | **yes** | |
| `nationality` | String(500) | **yes** | |
| `bsn_number` | String(500) | **yes** | BSN |
| `identification_number` | String(500) | **yes** | legacy identifier; duplicated semantics with BSN and vehicle plate |
| `reisdocument_type` | String(50) | no | paspoort/id-kaart/rijbewijs/overige |
| `reisdocument_nummer` | String(500) | **yes** | travel document |

### Contact
| Column | Type | Enc | Notes |
|---|---|---|---|
| `phone` | String(500) | **yes** | mirrors `contacts` row (`contact_type=phone`) |
| `email` | String(500) | **yes** | mirrors `contacts` row (`contact_type=email`) |

### Address (legacy flat columns, mirrored from `addresses` rows)
| Column | Type | Enc | Notes |
|---|---|---|---|
| `address` | String(500) | **yes** | combined free-text |
| `street` | String(500) | **yes** | |
| `house_number` | String(500) | **yes** | |
| `house_number_addition` | String(500) | **yes** | |
| `postal_code` | String(500) | **yes** | |
| `city` | String(500) | **yes** | |

### Financial / risk / notes
| Column | Type | Enc | Notes |
|---|---|---|---|
| `bank_account` | String(500) | **yes** | IBAN on the subject row; no source/provenance |
| `risk_score` | Integer, default 0 | no | 0-100 |
| `risk_factors` | SafeJSON | no | list of risk indicators |
| `notes` | Text | no | deprecated in favor of the `Comment` model |

### Entity (company/organization) / asset
| Column | Type | Enc | Notes |
|---|---|---|---|
| `registration_number` | String(100) | no | KVK |
| `legal_form` | String(100) | no | BV/NV/Stichting |
| `asset_type` | String(50) | no | |
| `estimated_value` | Numeric(15,2) | no | |
| `currency` | String(3), default EUR | no | |

### Online / social
| Column | Type | Enc | Notes |
|---|---|---|---|
| `social_media_ids` | SafeJSON | no | `{"facebook": {"id","username"}, ...}` |
| `workflow_social_accounts` | SafeJSON | no | workflow-only list `["@user", ...]`; not visible in CMS view |

### Vehicle / vessel
| Column | Type | Enc | Notes |
|---|---|---|---|
| `license_plate` | String(500) | **yes** | |
| `vin` | String(500) | **yes** | |
| `insurance_company` | String(500) | **yes** | |
| `brand` | String(100) | no | |
| `vehicle_type` | String(50) | no | |
| `imo_number` | String(500) | **yes** | |
| `mmsi` | String(500) | **yes** | |
| `eni_number` | String(500) | **yes** | |
| `vessel_nationality` | String(500) | **yes** | flag state |

### Data blobs (plaintext — review for the redesign)
| Column | Type | Enc | Notes |
|---|---|---|---|
| `vessel_data` | SafeJSON | no | full vessel lookup result |
| `rdw_data` | SafeJSON | no | full RDW record |
| `photo_path` | String(500) | no | uploaded photo path |
| `photo_metadata` | SafeJSON | no | EXIF/GPS/camera data — **plaintext location data** |
| `face_encoding` | SafeJSON | no | 128-float face encoding — **plaintext biometric data** |

### Tenancy / audit / soft-delete
`tenant_id` (NOT NULL), `is_deleted`, `deleted_at`, `created_by`, `created_at`,
`updated_at`.

## 3. Related models (key columns)

### `addresses`
`street`, `number` (house number + addition), `zipcode`, `town`, `country`
(all encrypted), `is_primary`, `kadaster_verified`, `kadaster_data`,
`kadaster_checked_at`, `subject_id`, `client_id`.

### `contacts`
`contact_type` (`email`|`phone`, NOT NULL), `value` (encrypted), `is_primary`,
`subject_id`, `client_id`.

### `social_accounts`
`platform` (NOT NULL, indexed), `username` (NOT NULL, indexed), `url`,
`account_id`, `finding_id`, `subject_id`. **All plaintext.** No source/status.

### `financial_records`
`case_id` (NOT NULL), `subject_id`, `transaction_date`, `amount` (**plaintext**),
`currency`, encrypted counterparty fields, `transaction_type`, `source`,
`source_reference`, `description`, `verification_status` (default `pending`),
`verified_by`, `verified_at`, `verification_notes`.

### `findings`
`case_id` (NOT NULL), `subject_id` (optional), `title`, `content`, `detail`,
`source_url`, `source_type`, `content_hash` (SHA-256 integrity),
`reliability_score` (default 5), `confidence_level`, `finding_type`,
`tags`, `icon`, `verified` (default False), `comment`, `raw_data`.

### `research_actions`
`case_id` (NOT NULL), `action_type`, `data_value` (Text; free-form target or
dork JSON payload), `label`, `status` (default `pending`), `started_at`,
`completed_at`, `error`, `result_summary`, `cancel_requested`, `archived_at`.
**No `subject_id`.** Linked to `findings` via `action_findings` junction.

### Associations
- `case_subjects`: `case_id` + `subject_id` only. No role/status/note.
- `subject_relations`: `subject_id` + `related_subject_id` + nullable
  `relationship_type`. Stored **bidirectionally** (two rows per link). No
  direction/source/confidence.

## 4. Two input paths

Subjects are created/edited in two independent places that write the same
table with divergent logic:

- **Path A — CMS standalone CRUD**: `subjects/create.html`, `subjects/edit.html`
  → `cms/routes/subjects_crud.py` (`create_subject`, `edit_subject`).
  Writes structured `Address`/`Contact` rows **and** syncs legacy subject
  columns.
- **Path B — workflow case screens**: `workflow_case_new.html`,
  `workflow_case_edit.html` → `cms/workflow/routes.py` (`_make_subject`,
  lines 408-446; edit 693-780). Writes only legacy columns; no
  `Address`/`Contact` rows; stores social handles in
  `workflow_social_accounts` JSON instead of `SocialAccount` rows.

## 5. Round-trip audit findings

"Round-trip" means: value entered on create → stored → edit form shows it → save
does not change it → detail view shows it.

### 5.1 Confirmed round-trip failures

1. **`registration_number` / `legal_form` silently dropped on edit.**
   Rendered in `edit.html` and accepted by the schema, but `edit_subject`
   never writes them (`_update_plain_fields` covers `_PERSON_TEXT_FIELDS` +
   `_VEHICLE_PLAIN_FIELDS` only). Edits to KVK number / legal form are lost.
   (`cms/routes/subjects_crud.py:635-645`; lists at 92-116.)
2. **`name` never recomputed on edit.** The edit submit handler does not call
   `computeName()` (create does), so the hidden `name` keeps its stale value
   and the edit guard (`data["name"] == subject.name`) skips the update even
   when split name fields changed. (`edit.html:746-761`, `subjects_crud.py:600`.)
3. **`risk_factors`, `asset_type`, `estimated_value`, `currency`, `legal_form`
   are written on create but never on edit.** (`subjects_crud.py:144-150`.)
4. **`identification_number` has no input on either path** (except vehicle in
   path B, where it is abused to store the plate) and is **not displayed** in
   the CMS detail view — despite the changelog claiming it was added to the
   person block. (CHANGELOG.md:95-96; zero references in
   `templates/cms/subjects/`.)
5. **`bank_account` is editable but never displayed** on the detail page.
   (`edit.html:399`, not shown in `view.html`.)
6. **Workflow path never sets `achternaam`.** The `_name` field (labelled
   "Achternaam") is stored only into `name`; `achternaam` stays NULL, so
   `compute_name()` renders the person's given name without the surname.
   (`workflow/routes.py:408-446`; display `workflow_case_detail.html:247`.)
7. **Vehicle plate double-stored in path B.** `{prefix}_identification` is
   written to both `license_plate` and `identification_number`
   (`workflow/routes.py:420`, `134`). In path A only `license_plate` is set,
   so CMS-created vehicles never appear in the RDW action picker (which gates
   on `identification_number`).
8. **Editing an `online` subject in path A silently converts its type.**
   `edit.html` type select omits `online`, so the first option (`person`) is
   submitted and the type is overwritten. (`edit.html:27-33`,
   `subjects_crud.py:613-622`.)
9. **Social handles are split across stores.** Path A writes `SocialAccount`
   rows (shown in CMS view, invisible in workflow detail); path B writes
   `workflow_social_accounts` JSON (shown in workflow detail, invisible in
   CMS view). No single source of truth.
10. **Workflow ignores structured address/contact rows it collects.** Hidden
    fields `subject_N_addresses_data`, `subj_{sid}_addresses_data/contacts_data`
    are populated client-side but never read server-side; `_json_field`
    (`workflow/routes.py:620`) is defined but never invoked. Path A parses the
    equivalents.
11. **`email`/`phone` on the subject row vs `Contact` rows diverge.** Path A
    syncs both; Path B writes only the row columns. An edit in one path can
    leave the other source stale.

### 5.2 Stored but never displayed

- `identification_number` (not shown in CMS view).
- `bank_account` (editable, never shown).
- `street`, `house_number`, `house_number_addition`, `postal_code`, `city`
  (individual columns only shown via `Address` rows or combined `address`).
- `risk_factors`, `asset_type`, `estimated_value`, `currency`.
- `social_media_ids` (CMS view uses `SocialAccount` rows instead).
- `workflow_social_accounts` (workflow detail only).
- `created_by` (never rendered).

### 5.3 Search on encrypted data is broken

There is **no full-text index** (no `tsvector`/GIN). Search is ILIKE-based and
runs against ciphertext for encrypted columns:

| Route | File:line | Encrypted column searched (matches nothing) |
|---|---|---|
| `/search` | routes/search.py:154-183 | `identification_number` |
| `/api/search/fts` | routes/search_fts.py:59-71 | `email`, `phone`, `identification_number`, `license_plate` |
| `/admin/global-search` | routes/search.py:545-570 | `identification_number` |

Only `name`, `notes`, and `SocialAccount.username` are genuinely searchable.
There is **no plaintext companion/hash/fingerprint column** for any encrypted
field. This is the design gap the keyed-fingerprint proposal (ADR-0001) closes.

### 5.4 Duplicated / ambiguous storage

- Legacy flat address columns mirror structured `Address` rows.
- `phone`/`email` columns mirror `Contact` rows.
- `identification_number` overlaps `bsn_number` and (for vehicles) the plate.
- Subject types: `company` **and** `organization` both exist; `online` is an
  ad-hoc type with no consistent model (a username lives in
  `workflow_social_accounts`, `social_media_ids`, or a `SocialAccount` row
  depending on path).
- `subject_relations` is bidirectional (two rows per link) with nullable type
  and no direction/source/confidence.
- `face_encoding` and `photo_metadata` are plaintext JSON (biometric/location).

### 5.5 Exports / reports surface

| Artifact | File:line | Subject fields |
|---|---|---|
| Case CSV export | routes/exports.py:84-97 | name, subject_type, risk_score, email, phone, address (decrypted) |
| Subjects CSV export | routes/exports.py:172-252 | name, type, risk, email, phone, address + primary Address, kadaster_verified, notes[:200] |
| Case JSON export | routes/cases_reports.py:25-54 | full `Subject.to_dict()` (all fields, decrypted) |
| Case report PDF | routes/cases_reports.py:426-538 | subject name only |
| Report template context | routes/templates.py:271-283 | name, subject_type, risk_score, address, email, phone |

No export distinguishes encrypted special-category data (BSN, bank, documents)
from ordinary fields; the redesign must add that gating.

## 6. Reproducing this inventory

Structural data (row counts, null percentages, type distributions, schema) can
be regenerated without touching application code or decrypting data:

```bash
# On the VPS (read-only connection; only aggregates leave the server):
sudo -u osint /opt/osint-dashboard/venv/bin/python \
  /opt/osint-dashboard/scripts/subject_model_audit.py

# Locally against any SQLite snapshot:
python3 scripts/subject_model_audit.py --db "sqlite:///test.db"
```

The script forces a read-only session (`SET TRANSACTION READ ONLY` on
PostgreSQL, `PRAGMA query_only = ON` on SQLite), introspects the schema, and
outputs JSON with row counts, per-column null percentages, and value
distributions only. It never reads, prints, or decrypts row data.

## 7. Manual acceptance tests (baseline)

These manual checks document today's behaviour and must keep passing after the
redesign:

1. Create a person (path A) with all person fields; open edit: every field
   shows the entered value; save; open view: all values visible.
2. Create a company with KVK + legal form; open edit; change KVK; save; reopen:
   the new KVK must persist (currently fails — issue 5.1.1).
3. Create a vehicle with a plate; confirm it appears in the workflow RDW
   action picker (currently fails for path A — issue 5.1.7).
4. Create an `online` subject; open it in edit (path A): the type must stay
   `online` (currently converts to `person` — issue 5.1.8).
5. Search by email/phone/BSN/plate: must find the subject (currently fails —
   issue 5.3).
6. Create a subject in the workflow, then open the CMS detail view: split name,
   addresses, contacts and social handles must all be visible and consistent
   (currently inconsistent — issues 5.1.6, 5.1.9-5.1.11).
<!-- PART3 -->
