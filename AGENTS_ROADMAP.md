# SaaS Roadmap — Iveras OSINT Dashboard

## Status
- **Branch:** `master` (SaaS work is merged — no more `saas-migration`)
- **Tests:** 292 passed, 4 skipped
- **Tenant isolation audit:** Clean (zero leaks verified)

---

## ✅ Already Done (SaaS Foundation)

- Multi-tenant data isolation (tenant_id on all tables, `apply_tenant_filter`, `ensure_tenant_access`)
- Self-service signup with join code + auto-provisioned tenant
- User roles: owner → admin → senior_investigator → investigator → junior_investigator → viewer
- RBAC decorators (`admin_required`, `roles_required`, `case_access_required`, etc.)
- Tier system: free (2 users, 5 cases) / starter (5, 50) / professional (25, 500) / enterprise (unlimited)
- Feature gating: `check_feature()` + `check_resource_limit()` for users and cases
- Stripe billing: checkout session, customer portal, webhooks (completed/updated/deleted)
- Invitation system: 48h token, email, limit check, accept flow
- API key management: generate, scopes, revoke, feature-gated
- Full audit logging: tenant-isolated viewer, per-user activity timeline
- In-app notifications: model + API, search restriction alerts
- Webhook notifications: login, signup, account events
- Super-admin tenant CRUD: list/create/edit/toggle/delete with UI
- Per-tenant statistics dashboard (cases, clients, subjects, findings)
- Login by email instead of username

---

## Phase 1 — Tenant Admin UI + Usage Analytics

### 1a. Tenant Admin UI
The existing super-admin tenant CRUD (`/cms/tenants`) is minimal. A proper admin panel is needed:

| Item | Description |
|---|---|
| Tenant detail page | Show tenant info, owner, current tier, subscription status, user count, case count, storage used, created date |
| Tenant user management | List users, invite, suspend, change role, reset 2FA (from tenant context) |
| Tenant billing view | Show Stripe subscription info, payment history, invoices, next billing date |
| Tenant activity log | Filtered audit log scoped to a single tenant |
| Tenant settings panel | Edit name, slug, domain, tier; toggle active/inactive with confirmation |
| Tenant suspension flow | Grace period, notify admin, data freeze, reactivation |
| Quick-tenant-switch | Super-admin can "log in as" or switch tenant context without logging out |

### 1b. Usage Analytics

| Item | Description |
|---|---|
| `UsageRecord` model | Daily per-tenant counters: api_calls, storage_bytes, active_users, cases_created, logins, exports |
| Background aggregation | Daily cron job that rolls up counts into UsageRecord |
| Tenant owner dashboard | Graphs: API calls over time, storage usage, active users, case growth — last 7/30/90 days |
| Super-admin overview | Aggregate view: total tenants, active/inactive, total MRR, storage cluster-wide, top N tenants by usage |
| Usage alerts | Email notification at 80%/100% of tier limit (storage, users, cases) |
| Billing analytics | MRR, ARR, churn rate, active subscriptions per tier, new signups per week |

---

## Phase 2 — Per-Tenant Configuration & Billing Lifecycle

### 2a. Per-Tenant Settings UI
The `TenantSetting` model + API exists but has **no dedicated UI**.

| Item | Description |
|---|---|
| Tenant settings page | UI for tenant owners to manage their own settings (not just super-admin) |
| Per-tenant feature flags | Let admins toggle features per tenant (currently all global) |
| Per-tenant external API keys | Brave, PimEyes, HIBP, SpiderFoot, etc. — tenants bring their own or use global fallback |
| Per-tenant SMTP | Email sending with tenant's own SMTP config |
| Per-tenant branding | Logo, colors, footer text, custom domain |

### 2b. Billing Lifecycle

| Item | Description |
|---|---|
| `invoice.payment_failed` webhook | Send dunning emails, retry logic, graceful downgrade after N days |
| Trial management | `trial_ends_at` field, `trial_will_end` notification, auto-convert or downgrade |
| Subscription cancellation | Self-service cancel with effective date, data retention period, reactivation window |
| Upgrade/downgrade proration | Calculate credits/charges on tier changes |
| Billing history UI | Show past invoices, payment methods, next billing date |
| Test/live mode indicator | Stripe test mode vs live visible in UI |

---

## Phase 3 — Quota Enforcement & Notifications

### 3a. Quota Enforcement (beyond users/cases)

| Item | Description |
|---|---|
| Storage quotas | Track uploads per tenant, block when limit reached |
| Subject/finding/document limits | Add to `check_resource_limit()` |
| Concurrent SpiderFoot scan limits | Per-tier limit on parallel scans |
| Per-tenant API rate limits | Separate rate limit buckets per tenant (not just global) |

### 3b. Notification Expansion

| Item | Description |
|---|---|
| Notification preferences | Per-user opt-in/out per notification category |
| Email notifications | Usage alerts, billing events, user joined/left, case assigned |
| Notification history page | View all past notifications, not just unread |
| Notification retention | Auto-purge old notifications (30/90 days) |
| Real-time push | Optional WebSocket for live notification delivery |

---

## Phase 4 — Polish & Enterprise

| Item | Priority | Description |
|---|---|---|
| Tenant data export | MEDIUM | Self-service ZIP download of all data (cases, subjects, findings, docs) |
| GDPR deletion workflow | MEDIUM | Full tenant wipe: anonymize user, delete data, confirm |
| Audit log retention UI | MEDIUM | Configurable retention period from settings page |
| Bulk invite (CSV) | LOW | Upload CSV of emails + roles, send batch invites |
| API key usage logging | LOW | Track per-endpoint per-key usage, audit trail |
| Key rotation reminders | LOW | Warn when API keys are old |
| Multi-currency billing | LOW | Support USD/GBP besides EUR |
| Coupon/discount UI | LOW | Apply Stripe coupons from admin panel |
| Immutable audit storage | LOW | Append-only audit log mode |
| Webhook event expansion | LOW | Add billing, tier, user lifecycle webhook events |
| SSO/SAML | LOW | Enterprise single sign-on |

---

## Notes

- **Tenant isolation is done** — all new development on models/routes must use `apply_tenant_filter()` and `ensure_tenant_access()`.
- **Branches**: Work directly on `master`; feature branches merged to `master`.
- **Testing**: 292 tests must stay green. Add tests for all new features.
