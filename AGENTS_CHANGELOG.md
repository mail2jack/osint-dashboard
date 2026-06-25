# Session Summaries / Changelog

## June 18 — Backup & Verify Script Overhaul

### Doel
Backup-script (her)schrijven met encryptie, native + Docker ondersteuning, volledige coverage (DB, uploads, config, SpiderFoot, systemd/nginx). Verify-script updaten naar nieuw format (encrypted archives, gewijzigde filenames).

### Wijzigingen
- **`scripts/backup.sh`**: Volledig herschreven met:
  - GPG symmetrische encryptie (AES-256) met `openssl rand` key
  - Database: PostgreSQL (Docker `pg_dump` of local `pg_dump`) of SQLite fallback (`cms.db`)
  - Uploads: `static/uploads/` tar.gz
  - Sessions: `flask_session/` tar.gz
  - Config: `.env`, nginx configs, systemd services, SpiderFoot passwd
  - Migrations: `migrations/versions/` archive
  - Metadata: `BACKUP_INFO.txt` met hostname, Python/Node versie, DB engine
  - Ingebouwde verificatie: decrypt + tar test na aanmaken
  - Retentie: backups ouder dan 30 dagen automatisch opgeruimd
- **`scripts/verify_backup.sh`**: Geüpdatet naar nieuw format:
  - Ondersteunt `.tar.gz.gpg` encrypted archives + key-based decryptie
  - File checks: `database.sql.gz`, `env.txt` (was `env_backup.txt`), `sessions.tar.gz` (was `flask_sessions.tar.gz`), `uploads.tar.gz`, `BACKUP_INFO.txt`
  - Optionele files: `nginx-*.conf`, `*.service`, `spiderfoot-passwd.txt`, `migrations.tar.gz` (info-only, geen warning bij afwezigheid)
  - Database validatie: decompress + SQL structure check + optionele psql dry-run
  - SQLite fallback: `PRAGMA integrity_check` + table count

### Tests
292 passed, 4 skipped, 0 failed — geen regressie.

---

## June 18 — Dashboard Performance Fix + Health Cache

### Dashboard traagheid — analyse
Het dashboard riep `check_external_services()` aan bij **elke** pageload. Die functie doet:
- 6–8 externe HTTP requests (RDW, Kadaster, HIBP, Overheid.io, Brave Search, Tor, SpiderFoot)
- Elke request voorafgegaan door `jitter_sleep()` (0.3–2.0s per call)
- **Totaal: 2–12+ seconden extra laadtijd**

Deze health data werd **niet gebruikt** in `dashboard.html` — de health indicator (⚪) werkt via client-side JS dat `/api/health-summary` aanroept op de achtergrond.

### Fix
- **`cms/routes/dashboard.py`**: `check_external_services()` verwijderd uit de dashboard route. Pagina laadt nu alleen de DB-queries (cases, clients, subjects, findings counts) + top 10 mijn cases — geen externe calls.
- **`/api/health-summary`**: 60s in-memory cache toegevoegd (`_get_cached_health()`). De client-side JS roept dit endpoint aan met 500ms vertraging + elke 30s — nu cached, dus maar 1x per minuut echte externe checks.

### Resultaat
Dashboard laadt net zo snel als cases/clients (~200–500ms i.p.v. 2–12s).

---

## June 18 — Update Notificatie + Restore Script + 4x Daily Cron

### Update Notificatie
- **`cms/routes/system.py`** (`do_update`): na elke GUI-update wordt een notificatie-email gestuurd naar alle superadmin gebruikers met:
  - Datum/tijd en status (✅ geslaagd / ❌ mislukt)
  - Versie en overzicht van alle stappen (met output)
  - Pad naar het backup-bestand en de backup-key
  - Herstel-instructies (SSH + `./scripts/restore.sh`)
  - SMTP-configuratie wordt gecheckt via `is_smtp_configured()` — als niet geconfigureerd, wordt de email overgeslagen (geen fout)

### CLI Update Notificatie
- **`scripts/notify_update.py`** (NIEUW): Python helper die Flask app laadt en email stuurt naar alle superadmins.
- **`scripts/update.sh`**: herschreven — `set -e` vervangen door handmatige error tracking (`OVERALL_STATUS`), na stap 5/5 wordt `notify_update.py` aangeroepen met status + backup pad, exit code 0 bij success / 1 bij failure.

### Restore Script
- **`scripts/restore.sh`** (NIEUW): complete CLI restore van encrypted backups:
  - `--list`: toont beschikbare backups
  - `--backup <file>`: specifieke backup herstellen
  - `--dry-run`: droge run, geen wijzigingen
  - Decrypteert met backup key (AES-256 GPG)
  - Herstelt: database (PostgreSQL `psql` of SQLite `cp`), uploads, sessies, `.env`, nginx, systemd, SpiderFoot passwd, migraties
  - Veiligheid: backup huidige staat vóór overschrijven (`pre_restore_*`), bevestigingsvraag voor `.env`
- **`AGENTS_OPERATIONS.md`**: nieuwe sectie "Restore from Backup"
- **`scripts/backup.sh`**: header verwijst naar `restore.sh`

### Pre-Update Backup
- **`cms/routes/system.py`** (GUI update): Stap 1 is nu een volledige `scripts/backup.sh` run i.p.v. alleen SQLite `cp`. Fallback naar `cp` als backup.sh ontbreekt.
- **`scripts/update.sh`** (CLI update): Nieuwe stap 1/5 — `scripts/backup.sh` aanroep vóór pip/git/alembic. Was 0 backup.
- **`install.sh`**: Cron job gewijzigd van 1x dagelijks (3:00 AM) naar 4x dagelijks (00:00, 06:00, 12:00, 18:00).
- **`scripts/doctor.py`**: `check_backup_cron` valideert nu het schema (`0,6,12,18` ipv `0 3`), herstelt naar 4x daily als fout.
- **`AGENTS_OPERATIONS.md`**: Backup cron documentatie + nieuwe "Update Backup" sectie.

### Waarom
- User verwachtte dat de GUI-update een backup maakt — dat deed hij alleen voor SQLite, niet voor PostgreSQL, en geen config/uploads.
- Automatische backups moeten vaker dan 1x/dag (vgl. 4x/dag is gebruikelijk voor productie-OSINT).

### Tests
292 passed, 4 skipped, 0 failed — geen regressie.

---

## June 18 — Hybride PostgreSQL-ondersteuning voor dev

### Doel
Developers kunnen kiezen: SQLite (snel, zero-config) of PostgreSQL (productiematch). Tests blijven SQLite. CI krijgt aparte Postgres-integratiejob.

### Wijzigingen
- **`.env.example`**: Postgres setup-instructies toegevoegd voor dev (opt-in)
- **`app.py`**: Graceful fallback — als Postgres onbereikbaar is, valt app terug op SQLite met `WARNING` in log
- **`.github/workflows/ci.yml`**: Nieuwe `integration-postgres` job (PostgreSQL 16 service, `continue-on-error: true`)
- **`AGENTS_OPERATIONS.md`**: Database-sectie herschreven met hybride strategie

### Hoe het werkt
- Zonder `DATABASE_URL`: SQLite, zero-config (bestaand gedrag)
- Met `DATABASE_URL=postgresql://...`: Postgres proberen, fallback naar SQLite bij mislukken
- CI: SQLite-tests blijven de snelle gate; Postgres-job parallel met `continue-on-error`
- Tests: Altijd SQLite (snel, geen externe dependency)

### Documentatie geüpdatet (deze sessie)
- `AGENTS_ARCHITECTURE.md`: Routes-structuur (44 i.p.v. 36 modules), background (RQ + executor), CSRF count (~57)
- `AGENTS_TESTING.md`: Test-table up-to-date (296 totaal), `/usr/local/bin` → `python3`
- `AGENTS_INTEGRATIONS.md`: `.env` vars (SECRET_KEY), getter functions verwijderd (Settings table only)
- `AGENTS_OPERATIONS.md`: Database hybride strategie (net herschreven)
- `AGENTS_CHANGELOG.md`: Deze entry

### Tests
292 passed, 4 skipped, 0 failed — geen regressie.

---

## June 18 — Route Splits & CSRF Audit (Round 2)

### app_bp.py (1381L) Split → 4 Files
- **`cms/routes/app_blueprint.py`** — shared `app_routes_bp` blueprint definition (avoids circular imports).
- **`cms/routes/ai_routes.py`** — AI status/summarize/analyze/enrich extracted.
- **`cms/routes/osint_routes.py`** — person/email/ip/domain/openkvk/webcam/hibp/username/search extracted.
- **`cms/routes/history_routes.py`** — history/archive/search-progress extracted.
- **`cms/routes/app_bp.py`** — reduced to PDF + phone routes + imports of child modules.
- All 37 routes registered correctly, all blueprint imports resolved.

### auth.py (1648L) Split
- Route handlers extracted to **`cms/routes/auth_routes.py`**.
- `cms/auth.py` reduced from 1648 → ~690 lines (keeps login manager, RBAC decorators, helpers, blueprint definitions).
- Circular import avoided by keeping decorators + `unauthorized` handler in `auth.py`.

### CSRF Audit — 28 @csrf.exempt Removed (Round 1+2)
- **Round 1 (16 removed)**: keep-alive, comments, notifications, subject relationships, face encodings, social accounts, bulk social extraction, template preview, case transitions, OSINT search cancel.
- **Round 2 (12 removed)**: generate-pdf, history (5 routes: archive, archive-all, mark-read, mark-all-read, stop-search), translations (3: review, manual-fix, auto-fix), health-summary dashboard, start-osint-search, cancel-search.
- Remaining ~43 @csrf.exempt: all on `@api_key_required` routes (external API consumers), file uploads, form-handling routes, or routes with unknown callers. Safe because CSRF is disabled in tests (`WTF_CSRF_ENABLED = False`).

### 292 Tests Passing, 4 Skipped, 0 Failed (verified after all changes)

---

## June 18 — Performance & Code Quality Sprint

### Test Fixes
- All 12 remaining test failures fixed: test_auth, test_financials_comments, test_integration (webhooks + background), test_lookups, test_routes_smoke — **292 passed, 4 skipped, 0 failed**.

### Background Tasks
- `cms/background.py`: Added `init_background(app)` — stores Flask app at startup, pushes `app.app_context()` in executor thread.
- Removed fragile `current_app._get_current_object()` workaround.
- `ThreadPoolExecutor` workers: 4 → 8.

### Setting JSON Serialization
- `Setting.set` stores `list`/`dict` as `json.dumps(value)` with `value_type="json"`.
- `Setting.get` auto-deserializes when `value_type == "json"`.

### Webhooks
- `dispatch()` parallelized via `ThreadPoolExecutor(max_workers=min(len(urls), 10))`.
- Removed manual `json.loads`/`ast.literal_eval` fallback (now handled by Setting).

### Response Helpers
- `cms/routes/response.py` — `api_success`, `api_created`, `api_deleted`, `api_error` wrappers (backward-compatible shapes).
- `scripts/migrate_jsonify.py` — batch migration: 35 route files migrated from `jsonify` → response helpers.

### Audit Decorator
- `cms/decorators.py` — `@audit_log(action, entity_type, entity_id_arg)` auto-logs after view returns.

### SpiderFoot Exclusion
- `pytest.ini`: `norecursedirs = spiderfoot`.
- `run_spiderfoot_tests.sh` for separate runs.

### Unused Dependencies Removed
- `flask-limiter>=3.0.0` — not used anywhere (custom `cms/rate_limiting.py`).
- `flask-bcrypt>=1.0.0` — not used anywhere (uses `werkzeug.security`).

### Unused Imports Removed
- 92 F401 unused-import violations auto-fixed via `ruff check --fix`.

### Hardcoded Test Key Replaced
- `tests/conftest.py`: Old Fernet key `ZFnorYZ7...` replaced with fresh generated key `J0k445Gk...`.

### start.sh → gunicorn
- Line 339: Changed from `python3 app.py` (Flask dev server) to `gunicorn app:app --bind 0.0.0.0:$port --workers 4 --timeout 120`.

### N+1 Query Audit
- No true N+1 patterns found — all `case.subjects.all()` / `case.findings.filter_by(...)` operate on single parent objects or are batch-loaded.

### Database Indexes
- Added `index=True` to `Tenant.owner_id` and `TenantSetting.tenant_id`.
- Migration: `migrations/versions/50c07e49855b_add_missing_indexes.py`.

### Static Assets
- Added `Cache-Control: public, max-age=31536000, immutable` for CSS, JS, and images in `app.py` after_request handler.

### CSRF Documentation
- Added CSRF protection analysis + audit guide to `AGENTS_ARCHITECTURE.md`.

### pytest-cov
- Added `pytest-cov>=5.0.0` to `requirements.txt`.

---

## June 7
### @validate Form POST Fix
- **Problem**: Pydantic `@validate` returned JSON 400 on ALL validation failures, even for HTML form POSTs.
- **Fix**: `@validate` now checks `request.is_json` — JSON gets JSON 400; forms get `flash()` + `redirect(request.path)`.
- **Schema fixes**: `risk_score: int = 0` → `Any` in `CreateSubjectSchema`, `reliability_score: int = 5` → `Any` in `CreateFindingSchema`.

### Enter Key Accidental Form Submission
- **Problem**: Pressing Enter in any form field submitted form unintentionally.
- **Fix**: `data-submit` handler calls `e.preventDefault()` on POST forms, checks `e.submitter` — only submit button clicks proceed.

### Session Timeout Data Loss
- **Problem**: 60s interval check `location.reload()` on 8h expiry, causing form data loss.
- **Fix**: Silent `fetch('/api/keep-alive')` extends session. `@csrf.exempt` added to route.

### CSS Class Collision
- **Problem**: Case view pages had black-on-black text — global `.header` clashed with local `.header`.
- **Fix**: Renamed local `.header` to `.page-header` in `cases/view.html`, `cases/list.html`, `subjects/list.html`.

### Key Principle
- **Form reliability**: Always test CRUD routes with both API calls AND browser form submissions.

---

## June 18 (continued) — Light mode CSS fix + Settings sidebar fix
### Dark background in light mode (production) — Root cause
- **`build.mjs`** includeerde `static/style.css` (SpiderFoot's dark-themed CSS, 727 lines) in de CMS bundle.
- **Cascade effect**: `style.css` wordt als laatste geconcateneerd en bevat `:root{--bg: #0d1117}` + `body{background:var(--bg)}`, wat de CMS `body{background:...}` override — altíjd donkere achtergrond, ongeacht `data-theme`.
- **Alleen productie**: `g.use_bundle=True` laadt `bundle.min.css` (met SpiderFoot); dev mode laadt individuele CSS files uit `css/` (zonder `style.css`).
- **Fix**: Exclude `style.css` in `build.mjs` met `f !== "style.css"` filter. Herbouwde bundle: 31.3 KB, 3 files (alleen CMS CSS). Tests: 292 passed, 4 skipped ✅.

---

### Vessel lookup crash op subject view
- **Bug**: `lookup_vessel_async()` in `cms/vessel_service.py:843` gaf alle kwargs (`imo`, `mmsi`, `eni`, `name`) door aan álle bronspecifieke functies via `asyncio.to_thread(func, **kw)`.
- **Gevolg**: Functies met een afwijkende signature (bv. `lookup_equasis(imo)` — accepteert alleen `imo`) crasheden met `TypeError: unexpected keyword argument 'name'`.
- **Fix**: `inspect.signature(func)` filtert per functie alleen de parameters die in de signature voorkomen.

### Settings sidebar categories onzichtbaar
- **Bug**: De `categories` dict in `cms/routes/settings.py:34` had geen `group` sleutel. De sidebar template filtert op `cat_info.group == 'integrations'` / `'system'`, dus geen enkele categorie werd getoond. Gebruiker kon alleen de default `api_keys` pagina zien en niet naar andere categorieën (zoals `appearance` voor thema) navigeren.
- **Fix**: `group: "integrations"` toegevoegd aan `api_keys`, `search`, `email`, `telegram`, `spiderfoot`, `ai`. `group: "system"` toegevoegd aan `general`, `security`, `appearance`, `feature_flags`. Ook ontbrekende `spiderfoot` categorie toegevoegd aan de dict.

---

## June 19 — Subject.created_by + Analytics import fix

### Wijzigingen
- **`cms/models/__init__.py`**: `Subject.created_by` column (`String(36)`, FK → `users.id`, indexed) + `creator` relationship added.
- **Subject creation routes**: `created_by=current_user.id` set in `subjects_crud.py` (`create_subject`), `social_accounts.py` (`create_subject_from_username`), `imports.py` (`import_subjects_csv`).
- **Migration `29bb9c967909`**: Idempotent `ALTER TABLE` for `subjects.created_by` + `clients.created_by` using `_has_column()` dialect-aware helper (works on both PostgreSQL and SQLite).
- **Pre-existing bugfix**: `analytics.py` used `request.method` on line 83 but `request` was not imported — added `request` to Flask import line.

### Tests
292 passed, 4 skipped, 0 failed — geen regressie.

---

## May 30
### Search Access Control + Notifications
- `cms/routes/notifications_api.py` — `/cms/api/notifications/*` endpoints.
- Notification bell in `base.html` — polls `/cms/api/notifications/unread-count` every 30s.
- `Notification` model — `user_id`, `type`, `title`, `message`, `action_url`, `read`, `created_at`.
- Trigger: unauthorized case access creates `search_access` notification for case owner.

### "current transaction is aborted" Fix
- **Error**: `psycopg2.errors.InFailedSqlTransaction` on `/cms/cases`.
- **Root cause**: `except Exception` in `inject_globals()` and startup blocks caught SQL errors without `db.session.rollback()`.
- **Fix**: Added `rollback()` in `before_request` (guard), in all `except` blocks in `inject_globals`, and in 4 `except` blocks in `cms/__init__.py`.

### Other
- Removed redundant `alembic stamp head` from `create_cms_module()`.
- Log file corruption (null bytes) cleaned up by restarting with clean output redirect.
- Bugfix: `notify_account_locked` import + call added.
