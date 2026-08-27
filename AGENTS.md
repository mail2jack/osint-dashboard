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

## Key Principles
- **RLS warning**: Productie PostgreSQL heeft Row Level Security. Directe `psql`-queries zonder `SET app.bypass_rls='true'` returnen lege resultaten — dit is géén data-verlies. Zie `AGENTS_OPERATIONS.md` voor details.
- **Debug mode**: `debug=True` in `app.py` is for development only. Set `debug=False` before production push.
- **API keys in Settings**: Prefer DB `Setting` table over `.env` for API keys. Only keep `DATABASE_URL`, `CMS_ENCRYPTION_KEY`, `FLASK_SECRET_KEY` in `.env`.
- **Session safety**: Always `db.session.rollback()` after catching exceptions that may originate from a DB query.
- **Thread safety**: Use `threading.Lock()` for shared state. No `shell=True` in `subprocess.run()`.
- **Form reliability**: Test CRUD routes with both API calls AND browser form submissions.

## Internationalization (i18n)
See each sub-doc for relevant i18n notes where applicable. Flask-Babel is used for UI strings only (not content translation).
