# Joost — next actions

**Status:** concept-roadmap from the 2026-09-26 local audit  
**Production:** protected; no production action is implied by this document

## Can be done independently in local staging

1. Add route-level authorization tests for global versus tenant settings.
   **Done locally:** tenant-settings isolation and non-admin denial are
   covered; global settings remain super-admin scoped in the route contract.
2. Add two-tenant export tests for CSV, PDF and JSON relations. **Done
   locally:** JSON/CSV relations and case-report filtering are covered;
   PDF generation/path safety is covered.
3. Add an RQ-worker contract test using a mocked queue and synthetic tenant.
   **Done locally:** enqueue contract and thread tenant-context coverage pass.
4. Reconcile the remaining Alembic differences without generating a bulk
   migration: model metadata, historical indexes and nullable/default rules.
5. Add an exact allowed-key test for telemetry payloads. **Done locally:**
   system-info and envelope keys are now explicitly asserted.
6. Add webhook payload redaction and destination-validation tests before any
   behavior change. **Done locally:** signing secrets stay in headers and are
   not placed in webhook bodies; destinations are limited to public HTTPS
   URLs without embedded credentials, duplicates, malformed entries, localhost
   or private IP literals. DNS-level egress enforcement remains an
   infrastructure concern. Tenant integration settings are seeded as empty,
   editable tenant fields; global values are never copied.

## Requires an owner/product decision first

1. Webhook and Twilio integrations are designated **per tenant**. The new
   HMAC dispatcher, legacy notification webhook and Twilio lookup are now
   tenant-scoped locally, and tenant administrators can configure the fields
   in Tenant Settings; existing global values still require an explicit
   migration/backfill plan before any deployment.
   Tenant-setting writes are now audit-trailed, tenant-scoped, and encrypted
   fields are re-encrypted on save; secret values are masked in the audit log.
2. Telemetry default is recorded: current fields are allowed and retained until
   replaced. Confirm provider-side retention, legal basis and customer notice.
3. Which external providers are approved for which types of personal data?
   **Prepared locally:** use `docs/PROVIDER-REGISTER-TEMPLATE.md` for the
   evidence-backed provider review.
4. WhatsApp default is recorded: disabled in production until explicit
   activation, separate account per tenant, manual sending first, and no
   automatic research data in messages. Verify operational pairing details
   before activation.

## Requires explicit production approval

1. Apply the API-key composite constraint migration to the production
   PostgreSQL database.
2. Inspect production schema/indexes and compare them with local staging.
3. Run production purge, backup, retention or secret-history operations.
4. Change production telemetry, webhook, notification or provider settings.
5. Deploy any branch or restart production services.

## Suggested order

1. Resolve the owner decisions about report inclusion, telemetry and
   integration scope.
2. Reconcile Alembic drift in small, reviewed groups.
3. Prepare a separate staging release checklist. **Prepared locally:**
   `docs/STAGING_RELEASE_CHECKLIST.md`.
4. Only then prepare production changes for explicit approval.

## Working rule

Every future implementation task should state its scope, branch, production
status, verification method and whether a user decision is required. No item in
this list authorizes a production mutation.
