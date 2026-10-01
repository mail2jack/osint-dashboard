# Joost — Project Context and Working Agreement

**Status:** living project document  
**Last reviewed:** 2026-09-26  
**Repository reviewed:** `osint-dashboard-workflow`  
**Reviewed revision:** `f2ee391a683c4253f8eb5366ce738fab7d2bb9af`

## Purpose

This document preserves the shared context and working agreements for future
Joost work. It is intended to prevent important decisions from living only in
conversation history.

The confirmed choices and remaining owner decisions are maintained in
[`docs/JOOST-DECISIONS.md`](JOOST-DECISIONS.md).

## What Joost is

Joost is an OSINT case-management application with cases, clients, subjects,
investigations, research actions, findings, evidence, reporting, users,
roles, tenant isolation, external OSINT integrations, encryption, audit logs,
background processing, backups and deployment tooling.

The current repository is a large Flask application using SQLAlchemy and
Alembic. Production is designed around PostgreSQL with Row Level Security
(RLS); SQLite remains available for local development and tests.

## Non-negotiable working agreements

1. Production is protected. Do not change, migrate, restart, deploy to, or
   otherwise mutate the production VPS without explicit user approval.
2. Investigate first, explain the implications, propose a plan, and only then
   implement after the user approves the scope.
3. Prefer isolated branches or worktrees for development. Keep unrelated work
   separate and reviewable.
4. Use plain Dutch explanations for the owner. Explain technical terms when
   they are necessary.
5. Treat tenant isolation, encryption, privacy, provenance and auditability as
   core product requirements, not optional polish.
6. Do not claim that a feature or control exists merely because a document
   describes it. Distinguish repository evidence, test evidence and live
   production evidence.
7. Never commit credentials, production database dumps, decrypted personal
   data, or other sensitive operational artefacts to source control.

## Current audit position

The read-only technical audit found a strong existing foundation, including:

- cases, clients, subjects and subject identifiers;
- investigations and research actions;
- candidate and verified findings;
- screenshots, source URLs and report evidence;
- audit logging and tenant-aware access controls;
- PostgreSQL RLS and integration tests;
- encryption and key-rotation support;
- Docker, Redis, background workers and deployment scripts;
- backup verification and disaster-recovery procedures;
- CI with linting, type checking, tests, dependency scanning and PostgreSQL
  integration coverage.

The audit did not contact or mutate production. Local Docker staging was used
for health checks, migrations and synthetic-data tests. Those local results do
not prove the live production state.

## Known priority concerns

1. [Resolved locally] The tracked `audit_archive_20260819.tar.gz` was removed
   from the working tree after explicit authorization. Git history and any
   operational copies still require separate secret-retention review.
2. Check Git history and operational logs for previously exposed secrets,
   including the TOTP exposure recorded in `STATUS_REPORT_20260821.md`.
3. Review the routes listed in `docs/CSRF_EXEMPT_CATALOG.md`, especially those
   using session cookies.
4. Confirm which branch is the official production source before implementation
   work is based on branch-specific behaviour.
5. Consolidate privacy, retention, provenance and source-confidence policy into
   explicit product documentation.
6. [Resolved locally] The API-key request-loader now rejects deactivated users
   before recording key use. The targeted contract test passes.
7. [Resolved in local PostgreSQL staging] API-key tenant integrity is now
   enforced by application checks plus migration `f4a5b6c7d8e9`, which adds a
   composite foreign key from `(api_keys.user_id, api_keys.tenant_id)` to
   `(users.id, users.tenant_id)`. Production rollout remains separate.
8. [Resolved locally at application and PostgreSQL level] API-key creation now requires an
   active target user in the effective tenant and stores that tenant explicitly.
   Cross-tenant HTTP coverage passes. PostgreSQL staging also enforces the
   pair with a composite foreign key; SQLite relies on application checks.
9. Define and enforce a tenant-context contract for background tasks. The
   generic runner uses an RLS bypass for task-status updates, so every task
   function must establish its own tenant context before touching tenant data.
   Add tests for both RQ and thread-fallback execution.
10. Keep the tenant-purge inventory aligned with schema changes. The expanded
    purge order and synthetic two-tenant deletion test pass locally; repeat
    PostgreSQL validation after future schema changes and before production use.
11. Formalize telemetry governance. Telemetry is enabled by default in code
   and can send hostname, local/public IP, OS/kernel, hardware capacity and
   app version to an external license service. Confirm the production toggle,
   purpose, retention, data region and contractual/privacy basis before
   treating this flow as finalized.
12. [Resolved locally and by owner decision] Official reports require
   `Finding.status == "verified"`; `include_in_report` can further exclude a
   verified finding. Raw working exports remain separate.
13. JSON/CSV export isolation and case-report tenant filtering are covered
   locally. PDF output/path safety is covered; an end-to-end staging PDF
   delivery test remains optional follow-up work.
14. Before risky production changes, establish a real staging baseline with
   PostgreSQL, Redis/RQ, synthetic tenants, mocked external integrations,
   monitoring and rollback evidence. The repository has strong deployment
   safeguards, but a complete independent staging environment is not visible
   here.
15. Manage technical debt deliberately. The application is a large Flask
    monolith: the central model module is roughly 4,700 lines, reviewed core
    modules total about 32,600 lines, and the route surface spans roughly 63
    route areas/files. Legacy fields are mirrored alongside newer structured
    and provenance data. Prefer gradual domain modularisation and explicit
    legacy-transition plans over a wholesale rewrite.
16. Alembic `check` in local PostgreSQL staging reports broad pre-existing
    model/schema drift (indexes, nullable/type differences, `clients.deleted_at`
    and constraint differences). Do not auto-generate a bulk corrective
    migration; reconcile these differences deliberately against the intended
    schema and production evidence.

## Roadmap order

### Phase 0 — safety and continuity

- Keep production unchanged.
- Establish the repository as the durable source of project context.
- Resolve the backup-archive and secret-history questions.
- Confirm the local development and review workflow.

### Phase 1 — security and governance

- Complete the CSRF-exemption review.
- Verify production PostgreSQL/RLS configuration when explicitly authorised.
- Define data classification, retention and deletion rules.
- Define provenance and confidence rules for external OSINT results.

### Phase 2 — maintainability

- Improve boundaries between identity, cases, investigations, findings,
  reporting, billing and integrations.
- Keep PostgreSQL integration coverage mandatory for tenant-sensitive changes.
- Reduce reliance on the large central model module where practical.

### Phase 3 — public company website

- Design the public website as a separate application and deployment boundary.
- Keep Joost on its own protected host/subdomain and expose only deliberate,
  minimal integrations between them.

## How future Codex tasks should start

Each task should state:

- the exact objective;
- whether it is read-only or allows changes;
- the repository and branch/worktree;
- what is explicitly out of scope;
- how the result will be verified;
- whether production access is needed (default: no).

Before implementation, update or reference this document when a decision affects
architecture, security, privacy, operations or the roadmap.
