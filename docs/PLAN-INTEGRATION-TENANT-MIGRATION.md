# Integration settings — tenant migration plan

**Status:** planning only; no values copied or deleted  
**Decision:** webhook and Twilio integrations are per tenant  
**Production:** not inspected or changed

## Current state

The runtime now reads these integration keys from `TenantSetting`:

- `webhook_urls` and `webhook_secret` for the HMAC dispatcher;
- `webhook_url` for the legacy notification path;
- `twilio_account_sid`, `twilio_auth_token`, `twilio_phone_number` and
  `twilio_whatsapp_number` for SMS/WhatsApp.

The old installation-wide `Setting` rows remain untouched as historical source
data. They are not used as an implicit fallback, because that could send one
tenant's events through another tenant's credentials.

## Required migration procedure

1. Perform a read-only inventory of populated global keys and all active
   tenants.
2. If exactly one tenant owns the existing installation configuration, prepare
   a dry-run report mapping each populated key to that tenant.
3. If multiple tenants exist, stop and require an explicit owner assignment;
   never copy credentials to every tenant.
4. Copy only after review, preserving encryption and recording an audit event.
5. Validate webhook and Twilio delivery with synthetic credentials and tenant
   A/B isolation tests.
6. Keep the old global rows quarantined until a separately approved cleanup.

No step above is automatic or authorized for production by this document.
