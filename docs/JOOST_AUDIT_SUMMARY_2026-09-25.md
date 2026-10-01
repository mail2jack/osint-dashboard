# Joost — Consolidated Technical Audit Summary

**Audit type:** repository audit with explicitly bounded local fixes and tests  
**Date:** 2026-09-25  
**Repository:** `osint-dashboard-workflow`  
**Reviewed revision:** `f2ee391a683c4253f8eb5366ce738fab7d2bb9af`  
**Production:** not accessed or changed

## Scope and limits

The repository, its documentation, tests, migrations, deployment files and
configuration examples were inspected. No production login, production
database query, deployment, migration, dependency installation or external
OSINT request was performed.

One local API-key contract test module was added and executed using synthetic
test data. The inactive-user API-key gap is now fixed locally and the targeted
contract tests pass.

The tenant-retention gap was also fixed locally: the dependency-aware purge now
includes the inventoried tenant tables, the direct tenant-delete route uses that
purge, and dry-run plus two-tenant isolation tests pass. PostgreSQL execution
still requires a PostgreSQL test environment.

## Overall assessment

Joost already has a strong foundation for a professional OSINT product:

- Flask/SQLAlchemy application with Alembic schema management;
- PostgreSQL production design with FORCE RLS;
- cases, clients, subjects, investigations and research actions;
- candidate/verified/rejected finding lifecycle;
- source URLs, source types, confidence and reliability metadata;
- evidence screenshots and capture provenance;
- reporting and PDF/CSV/JSON exports;
- users, roles, tenants and case-level access;
- encryption and key rotation;
- audit and login logging;
- background processing via RQ or thread fallback;
- Docker, Redis, healthchecks, backups and DR tooling;
- CI with tests, linting, typechecking and dependency scanning.

The main risks are now less about missing core functionality and more about
security invariants, governance, operational proof and accumulated complexity.

## Current local verification status — 2026-09-26

- Local Docker app, PostgreSQL and Redis are healthy.
- The WhatsApp service answers its health endpoint and is waiting for QR
  pairing, although Docker still reports its healthcheck as unhealthy.
- API-key, tenant-purge, export, background-task, migration-cycle, finding and
  report-policy regression tests pass locally.
- Ruff and mypy pass locally.
- Full local pytest run: 1,473 passed and 86 skipped. The remaining failures
  were isolated to the shared parallel Flask-login context, the local license
  `INSTALL_ID` mismatch (the license tests pass with `INSTALL_ID=test-install`),
  and sandbox-blocked browser tests that need local listening ports.
- A later read-only Docker status check was blocked by this Codex session's
  permission to the local Docker socket; no container action was attempted.
- The dependency vulnerability scan remains a CI-only check because the local
  environment cannot perform pip resolution without network/package metadata.
- Production was not accessed, migrated, deployed or otherwise changed.

## Findings by priority

### P0 — resolve before treating the security baseline as complete

1. **API-key owner state** — resolved locally: both API-key authentication paths
   now reject deactivated users.
2. **API-key tenant integrity** — resolved locally: application checks are
   backed by a PostgreSQL composite foreign key. Migration `f4a5b6c7d8e9`
   validates existing mismatches before installing the constraint; production
   rollout remains a separate, explicitly approved operation.
3. **API-key creation validation** — resolved locally at application and
   database level: the
   generation route now requires an active target user in the effective tenant,
   stores that tenant explicitly, and has a cross-tenant HTTP regression test.
   A PostgreSQL composite foreign key now prevents mismatched user/tenant
   pairs; SQLite relies on the application checks because the existing
   migration path cannot add this constraint there.
4. **Tenant purge completeness** — substantially improved locally: the purge
   now covers the inventoried tenant-bearing tables and the direct delete route
   uses it. PostgreSQL execution was validated in local Docker staging;
   production execution remains intentionally unverified.

### P1 — resolve before major production expansion

The full local serial test run completed with 1,489 passed and 86 skipped.
The remaining seven failures are environment/test-harness limitations: two
parallel/context-sensitive API-key checks and five browser tests that cannot
bind a local socket in the current sandbox. The affected API-key checks pass
when isolated.

5. **Export isolation** — JSON and CSV relation filtering now have passing
   two-tenant regression tests. The case-report context is explicitly
   tenant-filtered, and PDF generation has a passing output/path-safety test.
   Cross-tenant isolation for the general request-driven PDF remains not
   applicable because that route does not query tenant data.
6. **Background tenant contract** — the thread fallback now has explicit
   coverage proving it restores the persisted tenant context before execution.
   The opt-in RQ enqueue contract is also covered with a fake queue; a live
   worker test remains a staging task.
7. **CSRF exception assurance** — the 28-route catalog matches the code, but
   route-by-route negative tests should remain part of the security baseline.
8. **Staging parity** — create a real staging baseline before risky production
   changes: PostgreSQL, Redis/RQ, synthetic tenants, mocked external services,
   monitoring and rollback evidence.
9. **Backup governance** — the removed audit archive remains in Git history;
   history rewriting and credential-history review are separate decisions.
10. **Alembic model/schema drift** — local PostgreSQL `alembic check` still
    reports many operations: index additions/removals, nullable/type changes,
    the legacy `clients.deleted_at` column, and the composite users constraint.
    Do not generate or apply one bulk migration automatically; reconcile each
    difference against the intended schema and production evidence first.

### P2 — governance and product decisions

11. **Telemetry governance** — telemetry is enabled by default in code and can
    transmit hostname, local/public IP, OS/kernel, hardware capacity and app
    version. Confirm production setting, purpose, retention, data region and
    contractual/privacy basis. External flows are inventoried in
    `docs/EXTERNAL-DATAFLOW-INVENTORY.md`.
12. **Report policy** — resolved locally and by owner decision: official
    reports require `Finding.status == "verified"`; `include_in_report` can
    further exclude a verified finding. Raw working exports remain separate.
13. **Notification integration governance** — the active webhook and Twilio
    paths are now tenant-scoped locally, with empty tenant fields exposed to
    tenant administrators. Existing global values are intentionally not copied;
    destination validation, authorization coverage and any migration/backfill
    policy still need explicit review before professional multi-tenant use.
14. **External source policy** — provisional owner defaults are recorded:
    public technical data may use approved providers, business identifiers
    require configured provider approval, and sensitive personal data requires
    explicit provider approval. Provider contracts, retention and lawful-use
    evidence still need verification before production expansion.

### P3 — maintainability

15. **Monolith boundaries** — the central model module is roughly 4,700 lines;
    the reviewed core modules total about 32,600 lines and the route surface is
    spread across roughly 63 route areas/files. There are also multiple legacy
    compatibility and field-mirroring paths. Prefer gradual domain
    modularisation and explicit legacy retirement plans.
16. **Type coverage** — mypy has broad suppressions; retain it as a useful
    signal but do not treat it as proof that runtime boundaries are safe.

## What is already present versus desired architecture

| Desired capability | Repository status |
|---|---|
| Protected Joost application | Present |
| Login, roles and tenant isolation | Present; further API-key tests needed |
| Cases, subjects and investigations | Present |
| Research workflows | Present |
| Findings, validation and evidence | Present |
| Provenance and audit trail | Present |
| Reporting and exports | Present; JSON/CSV isolation tested, case-report context filtered, PDF output/path safety tested |
| Encryption of sensitive fields | Present |
| PostgreSQL/RLS production design | Present in code/tests; live state not rechecked |
| Docker/Redis/background processing | Present |
| Backups and DR tooling | Present in repository; live verifier state not rechecked |
| Public company website | Separate future project; not in this repository |
| Public website/Joost deployment separation | Future architecture decision |
| Independent staging environment | Not visible as a complete environment |

## Recommended execution order

1. Expand background-task coverage for
   both execution modes.
2. Reconcile the existing Alembic model/schema drift before relying on
   `alembic check` as a clean-release gate.
3. Decide telemetry, retention, provenance and report-inclusion policy.
4. Establish staging parity before risky production work.
5. Review backup/archive and secret-history governance.
6. Gradually modularise high-change domains and retire legacy paths.
7. Design the public company website as a separate application and deployment
   boundary.

## Working agreement

Production remains protected. Each implementation task must state its scope,
use an isolated branch/worktree where appropriate, verify changes locally and
wait for explicit approval before any production action.
