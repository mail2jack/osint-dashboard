# CSRF-Exempt Route Catalog

Every route in this catalog is exempted from Flask-WTF CSRF protection via
`@csrf.exempt`. Only these routes may be exempt. Do not add new exemptions
without updating this document and the allowlist guard in
`tests/test_csrf_protection.py`.

## Why exemptions are safe here

State-mutating routes rely on `CSRFProtect` running globally. Some routes are
intentionally exempt. The remaining risk of cross-site state mutation is
covered by three independent layers:

1. **SameSite session cookie**: `ProductionConfig` sets
   `SESSION_COOKIE_SAMESITE = "Strict"` (`cms/config.py`). Browsers do not
   attach the session cookie to cross-site POSTs, so cross-site requests
   reaching these routes run unauthenticated.
2. **API-key auth**: all OSINT/AI lookups carry `@api_key_required`. Callers
   without a session must present an `X-API-Key` header (not cookie-based, not
   CSRF-susceptible). Session-authenticated users pass through.
3. **Non-browser consumers**: Stripe webhooks and CSP violation reports are
   never issued by a browser and are exempt by design.

Routes that both mutate data and are reachable from the session-authenticated
browser frontend must NOT be exempt; they are enforced (see
`STATE_MUTATING_ROUTES` in `tests/test_csrf_protection.py`).

## Exempt routes (allowlist)

Legend — type: `lookup` read-mostly external lookup, `stream` SSE search,
`ai` AI call, `webhook` server-to-server, `report` browser violation report.

| URL | Method | Function | Blueprint | Type | Side effects |
|---|---|---|---|---|---|
| `/csp-report` | POST | `csp_report` | main app | report | none (CSP violation ingestion) |
| `/api/person/stream` | POST | `person_search_stream` | app_routes | stream | search-history / usage records |
| `/api/person` | POST | `person_search_json` | app_routes | lookup | search-history / usage records |
| `/api/email` | POST | `email_lookup` | app_routes | lookup | usage records |
| `/api/ip` | POST | `ip_lookup` | app_routes | lookup | usage records |
| `/api/domain` | POST | `domain_lookup` | app_routes | lookup | usage records |
| `/api/openkvk` | POST | `openkvk_lookup` | app_routes | lookup | usage records |
| `/api/webcam` | POST | `webcam_lookup` | app_routes | lookup | usage records |
| `/api/hibp` | POST | `hibp_check` | app_routes | lookup | usage records |
| `/api/username/stream` | POST | `username_search_stream` | app_routes | stream | search-history / usage records |
| `/api/email/stream` | POST | `email_search_stream` | app_routes | stream | search-history / usage records |
| `/api/email/holehe` | POST | `email_holehe` | app_routes | lookup | usage records |
| `/api/email/combined` | POST | `email_combined` | app_routes | lookup | usage records |
| `/api/email/crossvalidated` | POST | `email_cross_validated` | app_routes | lookup | usage records |
| `/api/username` | POST | `username_search` | app_routes | lookup | usage records |
| `/api/ai/summarize` | POST | `ai_summarize` | app_routes | ai | usage records |
| `/api/ai/analyze-query` | POST | `ai_analyze_query` | app_routes | ai | usage records |
| `/api/ai/enrich-profile` | POST | `ai_enrich_profile` | app_routes | ai | usage records |
| `/cms/api/phone-lookup-stored` | GET, POST | `phone_lookup_stored` | cms | lookup | cache read |
| `/cms/api/phone-lookup` | POST | `phone_lookup` | cms | lookup | cache write / usage records |
| `/stripe/webhook` | POST | `webhook` | stripe | webhook | billing state (Stripe signature verified) |
| `/cms/api/search/fts` | POST | `full_text_search` | cms | lookup | none (search of case data) |
| `/cms/api/email-check` | POST | `email_check` | cms | lookup | usage records |
| `/cms/check-policie-data` | POST | `check_policie_data` | cms | lookup | usage records |
| `/cms/api/kvk-lookup` | POST | `kvk_lookup` | cms | lookup | cache write / usage records |
| `/cms/api/kadaster-lookup` | POST | `kadaster_lookup` | cms | lookup | cache write / usage records |
| `/cms/api/politiebureau-lookup` | POST | `politiebureau_lookup` | cms | lookup | cache write / usage records |
| `/cms/check-rdw-vehicle` | POST | `check_rdw_vehicle` | cms | lookup | cache write / usage records |
| `/cms/api/vessel-lookup` | POST | `vessel_lookup` | cms | lookup | cache write / usage records |

## State-mutating routes that must NOT be exempt

These were previously exempt and had cross-site state-mutation risk from the
session-authenticated frontend. They are now CSRF-enforced
(`tests/test_csrf_protection.py`):

- `/cms/api/vessel/update-subject`
- `/cms/api/findings/from-vessel`
- `/cms/subjects/<id>/update-from-rdw`
- `/cms/api/findings/from-interpol`
- `/api/username/rapidapi`

## Operational note

`SESSION_COOKIE_SAMESITE = "Strict"` is the load-bearing layer for browser
requests. Verify it stays in effect for `ProductionConfig`; the guard test
`test_production_session_cookie_is_strict` in
`tests/test_csrf_protection.py` pins this down.