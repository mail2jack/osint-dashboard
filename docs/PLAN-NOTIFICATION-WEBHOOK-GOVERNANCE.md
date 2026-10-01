# Notifications and webhooks — security/privacy review

**Status:** local hardening implemented; no outbound production notification sent  
**Checked:** 2026-09-26

## What is present

Joost has multiple notification paths:

- an older single-URL webhook path for login, signup, user and case events;
- a newer multi-URL dispatcher with optional HMAC-SHA256 signing;
- SMTP email;
- Twilio SMS and WhatsApp;
- in-app notifications and background delivery.

## Important findings

### 1. Payloads can contain personal or operational data

Observed webhook payloads include usernames, IP addresses, email addresses,
organization names, case IDs, case titles and search-related information.
Those values can leave Joost and become subject to the receiver's logging and
retention policies.

### 2. Two webhook implementations have different security properties

The newer dispatcher supports a shared-secret HMAC signature. The older
`notifications.py` path posts directly to its configured URL without the same
signature mechanism. This makes receiver verification and consistent audit
behavior harder.

### 3. Destination validation is implemented locally

The active dispatcher accepts only public HTTPS destinations, rejects embedded
credentials and rejects localhost, loopback, private and link-local IP
literals. DNS-level egress enforcement remains an infrastructure concern.

### 4. Tenant scope is now the local contract

The webhook and Twilio lookup paths use `TenantSetting`. Configuration fields
are seeded per tenant, and cross-tenant reads/writes are covered by tests.
Existing global values are not copied automatically.

### 5. Legacy global settings require migration review

Older global values may still exist in the database, but the active notification
paths no longer use them. Any migration or removal of those values requires an
explicit backfill/retention decision.

Focused tests cover tenant-setting authorization, cross-tenant update
rejection, masking, encryption-on-save and audit logging.

## Recommended remediation order

1. Inventory every event and payload field.
2. Complete destination/redirect and DNS-level egress policy review.
3. Add redaction/minimization rules for IPs, emails, case titles and search
   terms.
4. Add route tests for owner/admin/super-admin access to global and tenant
   settings.
5. Add tests proving that a tenant cannot trigger delivery of another tenant's
   event data.
6. Document receiver retention, DPA and secret-rotation requirements.

Local hardening and regression coverage were implemented after the audit:
tenant-scoped settings and dispatch, public-HTTPS destination filtering,
secret masking/encryption, audit logging and cross-tenant authorization tests.
Production values, legacy-value migration and DNS/redirect infrastructure
controls remain outside scope.
