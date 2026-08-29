# Iveras OSINT Dashboard — Agent Guide

## Overview
This file is an index. Detailed documentation is split into focused files below.

## Quick Links
| Topic | File |
|---|---|
| Testing & conftest.py | [AGENTS_TESTING.md](./AGENTS_TESTING.md) |
| External integrations (SF, Phone, Vessel, AI, Telegram, etc.) | [AGENTS_INTEGRATIONS.md](./AGENTS_INTEGRATIONS.md) |
| Database, setup, deploy, encryption, git | [AGENTS_OPERATIONS.md](./AGENTS_OPERATIONS.md) |
| Internal architecture (routes, tasks, errors, validation) | [AGENTS_ARCHITECTURE.md](./AGENTS_ARCHITECTURE.md) |
| Frontend/UI (i18n, event delegation, keep-alive, help panel) | [AGENTS_UI.md](./AGENTS_UI.md) |
| OSINT OPSEC (jitter, proxies, Playwright, Tor) | [AGENTS_OPSEC.md](./AGENTS_OPSEC.md) |
| Monitoring (health, Sentry, Grafana, updates) | [AGENTS_MONITORING.md](./AGENTS_MONITORING.md) |
| Session summaries / changelog | [AGENTS_CHANGELOG.md](./AGENTS_CHANGELOG.md) |

## Working Style (user instruction, aug 2026)
- **Use subagents for task work.** Delegate problem-solving to subagents (e.g. `explore` for research, `general` for fixes) and split related tasks across parallel agents. Do not do it all inline; dispatch and coordinate, then verify their results.
- **Ask when needed.** If a decision, scope question, or ambiguity blocks progress — or something extra is needed (access, confirmation, choices) — ask the user rather than guessing. Ask before deploy/destructive actions.
- **Standard fix runbook**: research via subagents → implement → targeted tests → full suite serial → postgres integration → ruff/mypy → feature branch + PR (`gh pr create`) → wait for CI → `gh pr merge --merge --delete-branch` → deploy (below). Never commit to `master` directly.
- **Validation commands** (Python 3.12, use `python3` not `python`; run from repo root):
  - Full suite (serial): `python3 -m pytest -n 0 -q`
  - Postgres integration: `DATABASE_URL="postgresql://cms_test:cms_test@localhost:5432/cms_test" python3 -m pytest tests/test_postgres_integration.py tests/test_postgres_repair_integration.py tests/test_postgres_migration_rls.py -q --tb=short -n 0`
  - Lint: `python3 -m ruff check --select E4,E7,E9,F --ignore E402,E711,E712 cms/ tests/`
  - Typecheck: `python3 -m mypy cms/ --ignore-missing-imports`
- **Deploy**: SSH `root@joost.iveras.com`, app `/opt/osint-dashboard`, `scripts/production_rollout.sh --dry-run` first, then `--confirm DEPLOY-MASTER` after an explicit go from the user. Use `stdbuf -oL` on the remote command and a generous SSH timeout.
- **Repo location**: the active codebase is `/Users/gast/osint-dashboard`. `/Users/gast/Documents/monitor` is NOT the repo — it only holds the ADR-0001 status file.

## Key Principles
- **RLS warning**: Productie PostgreSQL heeft Row Level Security. Directe `psql`-queries zonder `SET app.bypass_rls='true'` returnen lege resultaten — dit is géén data-verlies. Zie `AGENTS_OPERATIONS.md` voor details.
- **Debug mode**: `debug=True` in `app.py` is for development only. Set `debug=False` before production push.
- **API keys in Settings**: Prefer DB `Setting` table over `.env` for API keys. Only keep `DATABASE_URL`, `CMS_ENCRYPTION_KEY`, `FLASK_SECRET_KEY` in `.env`.
- **Session safety**: Always `db.session.rollback()` after catching exceptions that may originate from a DB query.
- **Thread safety**: Use `threading.Lock()` for shared state. No `shell=True` in `subprocess.run()`.
- **Form reliability**: Test CRUD routes with both API calls AND browser form submissions.

## Internationalization (i18n)
See each sub-doc for relevant i18n notes where applicable. Flask-Babel is used for UI strings only (not content translation).
