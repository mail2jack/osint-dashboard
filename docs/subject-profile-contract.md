# Canonical Subject Profile Contract

Status: proposed target contract from the subject-first redesign PR1
(inventory phase). This defines the single source of truth for how subject
data is entered, edited, viewed, searched, acted upon, and reported. It is
**not yet implemented**; PR2+ implement it behind the `subject_first_investigations`
feature flag (ADR-0001).

## 1. Subject types

Six runtime types, backed by five domain types (one legacy alias removed):

| Domain type | Runtime value | Maps from today |
|---|---|---|
| person | `person` | `person` |
| organization | `organization` | `company`, `organization` (merge in a later migration) |
| vehicle | `vehicle` | `vehicle` |
| vessel | `vessel` | `vessel` |
| account | `account` | `online` (formalized) |

Rules:
- `subject_type` stays `String(20)`, indexed, NOT NULL.
- The ad-hoc `online` type is replaced by `account` and modelled as a typed
  entity whose primary identity is a platform + handle, stored in
  `subject_identifiers` (ADR-0001), not free-form JSON.
- Until the migration, the create/edit/API surface accepts the mapped values
  and refuses ambiguous input rather than silently converting (fixes issue
  5.1.8 in the inventory).

## 2. Field groups

Every field belongs to exactly one group. Groups are per-type; a type only
exposes its own groups.

| Group | Fields |
|---|---|
| Identity (person) | `achternaam`, `voornamen`, `voorletters`, `tussenvoegsels`, `name` (computed), `geslacht`, `date_of_birth`, `place_of_birth`, `nationality`, `bsn_number`, `reisdocument_type`, `reisdocument_nummer` |
| Identity (organization) | `legal_name`, `registration_number` (KVK), `legal_form`, `country_of_incorporation` |
| Identity (vehicle) | `license_plate`, `vin`, `brand`, `vehicle_type`, `rdw_data` |
| Identity (vessel) | `imo_number`, `mmsi`, `eni_number`, `vessel_nationality`, `vessel_data` |
| Identity (account) | `platform`, `username`, `profile_url`, `account_id` |
| Contact | `email` (0..n), `phone` (0..n), primary flags, source/status (ADR-0001) |
| Address | 0..n structured addresses, primary flag, source/status (ADR-0001) |
| Financial | `bank_account` (IBAN, 0..n), `asset_type`, `estimated_value`, `currency` |
| Online | 0..n `social_accounts` (platform, username, url, account_id, source) |
| Risk | `risk_score`, `risk_factors`, `notes` (→ `Comment` model) |
| Relations | 0..n `subject_relations` with type, direction, source, confidence (ADR-0001) |
| Metadata | `created_by`, `created_at`, `updated_at`, `is_deleted` |

## 3. The single contract

One shared read/write service (`SubjectService`) is the only code path that
reads or writes subject storage. The standalone CRUD routes **and** the
workflow routes both call it; neither path manipulates columns directly.

### 3.1 create(input) → Subject
- Accepts the per-type field group(s) for the chosen `subject_type`.
- Computes `name` server-side; never trusts a client-computed value.
- Stores identifiers/contacts/addresses/social accounts through the fact layer
  (ADR-0001) with `source="manual"`, `status="candidate"`.
- Records an audit entry for every created value.

### 3.2 edit(subject, input) → Subject
- Round-trips every field of the subject's groups: the form is rendered from
  the database read-model (never browser state), and every editable field is
  written back.
- Explicit add/update/remove operations for addresses, contacts, and social
  accounts — never silent replace/wipe (removes issue 5.1.10).
- `name` is always recomputed from split fields.
- Encrypted fields are re-encrypted on write; nothing is stored decrypted.

### 3.3 view(subject) → dict
- Renders exactly the database read-model. Displays `identification_number`,
  `bank_account`, and split address columns (fixes 5.1.4, 5.1.5).
- Masks special-category values by default (BSN, travel documents, bank data,
  biometrics) unless the viewer explicitly reveals them; masks are the default
  even for authorized viewers, with audit.

### 3.4 search(query) → subjects
- Searches only through keyed fingerprints (ADR-0001): normalized, keyed,
  HMAC-signed hashes — never plaintext and never ciphertext ILIKE (fixes 5.3).

### 3.5 actions / reporting
- Actions carry an explicit `subject_id` (PR4) or an explicit
  `case-wide` scope flag; never implicit "first subject of the case".
- Reports/exports consume the same read-model and must be able to exclude
  special-category fields by default.

## 4. Round-trip guarantee

For every type and every field in its groups:

```
create(input) -> edit shows input -> save() -> reopen edit -> view shows value
```

PR2 adds one regression test per type asserting this for every field in the
type's groups, including encrypted fields, addresses, contacts, bank data,
social accounts, RDW data, and vessel data.

## 5. Acceptance criteria (per PR)

1. No action runs without an explicit subject or an explicit case-wide scope.
2. No paid channel is used without an explicit tenant opt-in.
3. Every edit is fully round-trip correct for all fields in the subject's
   groups.
4. Every automatic result carries source, action, timestamp, and status.
5. Only validated facts reach formal reporting by default.
6. A user only ever sees subjects/facts/actions within cases they can access,
   within their tenant (case-isolation and tenant-isolation tests required).
7. No silent type conversion, no silent address/contact replacement.
8. Every PR ships: migration up/down plan, tenant + case isolation tests,
   round-trip tests for changed fields, a short manual acceptance test list,
   and requires explicit approval before merge.
