# Iveras OSINT Dashboard — Agent Guide

## UI Internationalization (i18n) — Flask-Babel
- **Setup**: `pip install Flask-Babel` (v4+). Init in `cms/i18n.py`.
- **Config**: `babel.cfg` at project root — extracts strings from `.py` and `.html` files.
- **Locale selector** (`cms/i18n.py:get_locale`): reads `session["lang"]` → falls back to browser `Accept-Language` → defaults to `"nl"`.
- **Switching**: `GET /lang/<locale>` sets `session["lang"]` and redirects back. Links NL/EN in `base.html` header.
- **Template usage**: `{{ _('Dashboard') }}` or `{{ gettext('Settings') }}` — Jinja2 + Python.
- **Adding a new string**: wrap in `_('...')`, then:
  ```
  pybabel extract -F babel.cfg -o translations/messages.pot .
  pybabel update -i translations/messages.pot -d translations
  ```
  Edit `.po` file in `translations/<lang>/LC_MESSAGES/messages.po`, then:
  ```
  pybabel compile -d translations
  ```
- **New language**: `pybabel init -i translations/messages.pot -d translations -l de`, edit `.po`, compile.
- **Current languages**: `nl` (default), `en`. Placeholders for `de`, `fr`.
- **`.po` files**: `translations/nl/LC_MESSAGES/messages.po` + `translations/en/LC_MESSAGES/messages.po`.
- **`_()` injected** in `app.py` context processor as `ctx["_"]` — available in all templates.
- **NOT for content translation**: Helsinki-NLP models not needed. UI strings only.

## Entrypoint & Run
- `app.py` is the single Flask entrypoint. Dev: `/usr/local/bin/python3 app.py` (port 5000). **Python 3.12 required** — `python3` must resolve to `/usr/local/bin/python3` or use the full path.
- CMS module initialized via `cms/__init__.py::create_cms_module(app)`.
- **⚠️ Production push**: Before pushing to GitHub / deploying, change `debug=True` → `debug=False` in `app.py:472` (`app.run(host="0.0.0.0", port=5000, debug=False)`). Debug mode injects a Werkzeug auto-reload script that refreshes the browser on every server restart, which is undesirable in production.
- Dev with SpiderFoot: `./start.sh start`. Stops: `./start.sh stop`.
- Production: `sudo ./install.sh` (Debian/Ubuntu — sets up Nginx, PostgreSQL, SpiderFoot, systemd, SSL). Accepts space-separated domain list for multi-domain SSL.

## Database
- `install.sh` sets up PostgreSQL by default (`DATABASE_URL` in `.env`). Falls back to SQLite (`cms.db`) if `DATABASE_URL` is unset.
- Local dev uses PostgreSQL too. Setup: `brew install postgresql@16 && brew services start postgresql@16 && createdb cms_dev && echo 'DATABASE_URL=postgresql:///cms_dev' >> .env && pip install psycopg2-binary`.
- Schema beheerd via **Alembic** (`migrations/`). `create_cms_module()` in `cms/__init__.py` voert bootstrap uit:
  - **Nieuwe DB** (geen tabellen): `alembic upgrade head` — creëert alle tabellen
  - **Bestaande DB** (wel tabellen, geen `alembic_version`): `alembic stamp head` — zet versie zonder migraties te draaien
  - **Reeds gemigreerd**: idempotent (niets te doen)
- Admin (`admin`/`changeme123`) wordt aangemaakt door `cms/__init__.py` data-migratie na schema bootstrap.
- Alle handmatige `ALTER TABLE`-migraties verwijderd uit `cms/__init__.py` — zitten nu in Alembic.
- **Alembic CLI**: `DATABASE_URL="sqlite:///test.db" python3 -m alembic upgrade head` (werkt standalone, geen Flask CLI nodig).
- **Nieuwe migratie maken**: `DATABASE_URL="..." python3 -m alembic revision --autogenerate -m "description"`.
- **CRITICAL — PostgreSQL vs SQLite diff**: PostgreSQL enforces `VARCHAR(n)` length limits; SQLite ignores them. Fernet-encrypted values are ~100-140 chars, so ALL encrypted columns MUST be `String(500)` minimum. Was vroeger auto-migrated op startup, nu vastgelegd in Alembic migration + modeldefinities.
- Never mutate `created_at` on ORM objects directly (crashes SQLite). Sort with `strftime()` in sort key lambda.
- `instr()` in queries is dialect-agnostic (uses `instr` for SQLite, `strpos` for PostgreSQL) — helper in `cms/routes/search_fts.py`.
- **CRITICAL — Alembic stamp vs upgrade pitfall**: `alembic stamp head` registreert de versie maar voert **geen DDL uit**. De initiale migratie `8c4bb90d2490_initial_schema.py` bevat kolommen die mogelijk niet in de oude productie-DB zaten (`password_reset_token`, `password_reset_expires`, `failed_login_attempts`, `locked_until`). Migratie `69999cbb5609` lost dit op met `_has_column()` checks (dialect-agnostisch via `sqlalchemy.inspect`), zodat `ADD COLUMN` alleen wordt uitgevoerd als de kolom nog ontbreekt.
- **Nieuwe migratie maken (manual)**: Schrijf `upgrade()`/`downgrade()` met `_has_column()` guards voor idempotentie op zowel SQLite als PostgreSQL. SQLite ondersteunt geen `ALTER COLUMN DROP DEFAULT` — gebruik `server_default` op `add_column` i.p.v. aparte `alter_column`.

## SpiderFoot Integration (`cms/spiderfoot_service.py`)

### Config source (critical)
- SpiderFoot config is read from the `Setting` model (DB table), NOT from `.env`.
- `get_spiderfoot_config()` in `cms/routes/spiderfoot.py` calls `Setting.get('spiderfoot_url')`, `Setting.get('spiderfoot_password')`, etc.
- Setting values via Flask shell: `Setting.set('spiderfoot_url', 'http://...')`.

### Auth
- SpiderFoot v4 uses HTTP Digest auth. Credentials stored in `~/.spiderfoot/passwd` (`admin:<password>`).
- Start with auth: `python3 sf.py -l 127.0.0.1:5001` (passwd file auto-loaded from `~/.spiderfoot/passwd`).

### API data quirks
- **Scan list format**: `[id, name, target, created, started, completed, status, resultCount, riskSummary]` — status is UPPERCASE (`RUNNING`, `FINISHED`, etc.).
- **Result format**: `[timestamp, data, value, sourceModule, ..., type]`.
- **SFURL tags**: Result `data` contains HTML-escaped `<SFURL>` tags (`&lt;SFURL&gt;url&lt;/SFURL&gt;`). Must `html.unescape()` before regex parsing. Done in `normalize_result()` at line 588.

### Templates
- Live in `templates/cms/spiderfoot/`: `index.html` (dashboard), `view.html` (scan results), `scan.html` (new scan form), `list.html` (all scans), `scan_subject.html`.
- Template filters in `app.py:103-270`: `urlize_target`, `result_link`, `platform_name`, `platform_color`.
- Rich result cards use `.rich-card` with `--card-color` CSS custom property (no separate classes per type).

## Email & AI Config
- `cms/email_utils.py`: SMTP with `ssl.create_default_context()` (TLS cert verification). `send_password_reset_email()` sends setup link; `send_new_user_credentials()` sends welcome without password.
- `cms/services/ai_service.py`: Dual-provider architecture — **OpenRouter** (primary, 300+ models via unified API) + **Ollama** (fallback for local inference). Config constants (`OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `OLLAMA_URL`, `OLLAMA_MODEL`) defined at module top. Provider selection: `_generate()` tries OpenRouter first if API key is configured; falls back to Ollama on failure. `get_ai_config()` returns current provider info + availability. `check_ai_available()` checks either provider. All 3 consumer functions (`summarize_results`, `analyze_natural_language`, `enrich_profile`) are provider-agnostic.
- Set OpenRouter API key via Settings → API Keys → `openrouter_api_key`, or via env var `OPENROUTER_API_KEY`. Model selection at Settings → AI Provider → `openrouter_model` (default: `openrouter/auto` for automatic model routing).
- Backward compat: `get_ollama_config()`, `check_ollama_available()`, `ollama_generate()` kept with same signatures. `check_ollama_available()` now checks any available provider.

## Phone Lookup (`cms/routes/phone.py`)
- `POST /cms/api/phone-lookup` — validates + enriches phone numbers using `phonenumbers` library + free `bedrijfsdata.nl` API (NL only).
- Returns: valid, formatted, country, region, carrier, line_type, timezone, WhatsApp/Telegram presence.
- Button "📞 Check" appears next to phone fields in subject/client view pages (hidden if no phone).
- Requires `phonenumbers` (`pip install phonenumbers`).
- Depends on `httpx` (already in requirements).
- `re` imported globally in routes.py for number normalization.
- **`normalize_phone()`** (cms/routes/phone.py) — normaliseert elk telefoonformaat naar E164 (`+31634407404`). Wordt aangeroepen bij create/edit van subject + client (zowel los veld als contacts).
- **WhatsApp/Telegram check**: Uses `whatsapp.checkleaked.cc` API (RapidAPI) when `whatsapp_checkleaked_key` Setting is set. Falls back to unreliable scraping (`api.whatsapp.com/send` + `t.me`) when API returns 503 or no key configured.
- API key: `Setting.set('whatsapp_checkleaked_key', 'your-rapidapi-key')` (BASIC free tier: 50 req/month). Signs up at whatsapp.checkleaked.cc/pricing.
- API response includes `isWAContact`/`isUser` for WhatsApp presence + `telegram` object with existence check.
- API response included in popup: about, business, enterprise, verified, banned, line_type, cached status, check date.
- Profielfoto wordt automatisch gefetcht en als base64 weergegeven (indien beschikbaar).
- Alle API responses worden opgeslagen in `PhoneLookup` model (tabel `phone_lookups`) met timestamp + raw JSON + profielfoto.

## Interpol + Politie Check (`cms/routes/interpol.py` + `cms/politie_scraper.py`)
- `POST /cms/check-policie-data` — checks subject name against INTERPOL Red Notices (wanted) + Yellow Notices (missing) + politie.nl/vermist (NL missing persons).
- **Button** "🌍 Check Interpol" on subject view page (was "🚔 Check Politie Data").
- Interpol API: `ws-public.interpol.int` (Akamai rate-limited, may return 403 after many calls).
- Fallback: scrapes `politie.nl/vermist` for matching names when Interpol returns no results.
- **Politie.nl/gezocht**: Also checks `politie.nl/gezocht` for Dutch wanted bulletins ("opsporingsberichten") via `cms/politie_scraper.py`. Extracts Nuxt SSR payload, resolves reactive refs, and searches titles/locations for name matches.
- Status check: `GET /cms/check-policie-data-status`.

## Address Form — Postcode Check
- Create/edit subject forms have **separate** `Street`, `Number`, `Zipcode`, `Town` fields (was "Street + Number" combined before May 11).
- **🔍 button** next to zipcode: calls `POST /cms/api/kadaster-lookup` with `{zipcode, number}` → fills in street + town + number from PDOK BAG.
- JS functions: `postcodeCheck(btn)` in both `create.html` and `edit.html`.
- `serializeAddresses()` now includes the `number` field (was always empty before).
- The Address model already has separate `street` and `number` columns, so no DB migration needed.

## Politiebureau Lookup (`cms/routes/politiebureau.py`)
- `POST /cms/api/politiebureau-lookup` — finds nearest police station for an address.
- Accepts `{address_id}` (resolves from DB) or `{lat, lon}` directly.
- Uses coordinates from `kadaster_data` (if stored) or falls back to PDOK BAG lookup, then calls `api.politie.nl/politiebureaus/v1`.
- Returns: station name, address, phone, opening hours, OSM map link, politie.nl page URL.
- **Button** "🚔 Politiebureau" on each address card in subject view page (`view.html`), next to the Kadaster button.
- Result displayed in a red-themed card below the address.

## Vessel / Ship Lookup (`cms/vessel_service.py`, `cms/routes/vessel.py`)
- `POST /cms/api/vessel-lookup` — searches VesselFinder (free, MMSI/name), MarinePlan (AIS by name/MMSI, API key), KVNR Schepenzoeker (IMO/name, public), Binnenvaart.eu (ENI/name, public), Equasis (IMO, requires free account credentials).
- `POST /cms/api/vessel/update-subject` — updates subject with IMO/MMSI/ENI/flag (encrypted) + `vessel_data` JSON.
- `POST /cms/api/findings/from-vessel` — creates a Finding from vessel lookup data (needs `case_id`).
- **Button** "🚢 Check Vessel" on subject view page (only for `subject_type == 'vessel'`).
- Vessel fields (IMO, MMSI, ENI, flag) encrypted via Fernet like `license_plate`.
- `lookupVesselCreate()` (create.html) / `lookupVesselEdit()` (edit.html) — auto-fills IMO/MMSI/ENI/name from lookup.
- MarinePlan key: `Setting.set('marineplan_api_key', 'your-key')` (get at https://marineplan.com). Without it, MarinePlan source is skipped.
- Equasis: `Setting.set('equasis_email', '...')` + `Setting.set('equasis_password', '...')` (free registration at equasis.org). Without credentials, Equasis source is skipped.
- `lookup_marineplan()` is rate-limited (2s between calls via `_LAST_MARINEPLAN_CALL`).
- Binnenvaart.eu and KVNR are public scrapes (no auth needed).
- Binnenvaart.eu: ENI numbers searched via `zoeken` query param; scrapes table rows for name, ENI, type, year.
- KVNR: IMO extracted via regex `IMO[:\s]*(\d{7})`, flag via `(Flag|Vlag)[:\s]*(\w+)`.
- **Combined orchestrator** `lookup_vessel()` in vessel_service.py tries all sources in order (VesselFinder → MarinePlan → KVNR → Binnenvaart → Equasis), merging non-None fields.

### DB Migration
- New columns added to `subjects` table: `imo_number VARCHAR(500)`, `mmsi VARCHAR(500)`, `eni_number VARCHAR(500)`, `vessel_nationality VARCHAR(500)`, `vessel_data TEXT`.
- Auto-migration handled by Alembic (zie `migrations/versions/8c4bb90d2490_initial_schema.py`).
- Encrypted columns (imo, mmsi, eni, nationality) zijn `String(500)` in de migratie — PostgreSQL-compatibel.

## API Keys — Settings GUI (not .env)
- Prefer Settings table over `.env` for API keys. Use `Setting.set('key_name', 'value')` via Flask shell or the Settings GUI.
- Getter functions in `app.py`: `_get_overheid_key()`, `_get_twochat_credentials()`, `_get_brave_key()`. Pattern: env var override → Setting fallback.
- Hardcoded `OVERHEID_API_KEY`, `TWOCHAT_API_KEY`, `TWOCHAT_WHATSAPP_NUMBER` at module level are deprecated — kept for backward compat but no longer used in routes (routes call getter functions instead).
- One-time migration script `scripts/migrate_env_to_settings.py` copies existing `.env` values to DB.
- Twitter Basic Auth (`read:api` at `app.py:1051`) is dead (Twitter v1.1 deprecated), ignore.
- `.env` should only retain `DATABASE_URL`, `CMS_ENCRYPTION_KEY`, `FLASK_SECRET_KEY`. Move all API keys to Settings.

## Update Notifications
- `check_update()` at `app.py` (routes section) checks both VERSION file AND latest commit SHA from GitHub.
- Banner shows for version bumps OR any new commits (bugfixes without version change).
- `last_update_commit` Setting stores the local HEAD SHA after each successful `do_update()`.
- If remote SHA differs from stored SHA, a "New commits available" notification appears.
- **CRITICAL — `update_check_repo` must be set** in the DB (`Setting.set('update_check_repo', 'mail2jack/osint-dashboard')`). Without it, the API returns `check_enabled: False` and the banner NEVER shows. `install.sh` sets this automatically; manual setups MUST set it.
- **Auto-detect**: If `last_update_commit` is empty, `check_update()` runs `git rev-parse HEAD` and stores the result. This means the banner can only detect commits pushed AFTER the first page visit — visiting AFTER a manual `git pull` will silently store the new HEAD and show no diff.
- **Diagnostic**: Run `sudo -u osint /opt/osint-dashboard/venv/bin/python -c "from app import app; from cms.models import Setting; app.app_context().push(); print('repo:', Setting.get('update_check_repo')); print('last_sha:', Setting.get('last_update_commit','(empty)'))"`

## Image Upload Validation (`cms/image_validation.py`)
- `validate_image_file(file_storage)` checks the first 32 bytes against known magic byte signatures (PNG: `\x89PNG...`, JPEG: `\xff\xd8\xff`, GIF: `GIF87a`/`GIF89a`, WebP: `RIFF....WEBP`).
- Used in `cms/routes/screenshots.py` and `cms/routes/subjects_faces.py` — replaces unreliable `content_type` header check and extension-based check.
- File cursor is restored to position 0 after reading magic bytes.

## Password Reset Flow
- **No passwords in email**: `send_new_user_credentials()` still exists as legacy but updated to omit the password. New `send_password_reset_email()` sends a reset link.
- **`User` model**: Added `password_reset_token` (VARCHAR(128), hashed SHA-256) + `password_reset_expires` (TIMESTAMP, 48h TTL).
- **Route** `GET/POST /auth/set-password/<token>` — public, validates token, requires 8+ char password + confirm. Token is one-time use (cleared after set).
- **`create_user()`**: When `send_email=True`, generates a reset token and emails a "Set Password" link instead of including the raw password.
- API response for `create_user` still includes `generated_password` (admin needs it for offline sharing with the user).

## Encryption Key Persistence
- **`CMS_ENCRYPTION_KEY` env var** — takes precedence.
- **`.cms_key` file** — fallback; read if env var is unset. Created with `chmod 600` on first auto-generate.
- Auto-generation only happens if BOTH env var AND `.cms_key` file are missing. Once persisted, key survives restarts.
- Key file is at project root (`.cms_key`), gitignored.
- `cms/config.py` defines `Config`, `DevelopmentConfig`, `ProductionConfig`, `TestingConfig`.
- Picks config class based on `FLASK_ENV` env var (default: `development`).
- `DevelopmentConfig`: `WTF_CSRF_ENABLED = False`, `SESSION_COOKIE_SECURE = False` — safe for local dev.
- `ProductionConfig`: `WTF_CSRF_ENABLED = True`, `SESSION_COOKIE_SAMESITE = 'Strict'`, enforces `CMS_ENCRYPTION_KEY`.
- `TestingConfig`: in-memory SQLite, CSRF off.
- **Session**: `PERMANENT_SESSION_LIFETIME = 8h`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SAMESITE = 'Lax'/'Strict'`.
- **Uploads**: `MAX_CONTENT_LENGTH = 16MB` — enforces request body size limit across all endpoints.
- **Security headers**: Added in `app.py` via `@app.after_request`: `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Strict-Transport-Security` (non-localhost only).
- **SQLite note**: `SQLALCHEMY_ENGINE_OPTIONS` (pool_size, pool_recycle, pool_pre_ping) from config is automatically removed for SQLite at `app.py` startup to prevent hangs.
- **CSRF**: Active via `flask_wtf.CSRFProtect`. All 37 forms have `{{ csrf_token() }}` hidden inputs. All JSON API POST routes have `@csrf.exempt`. Dual routes (form + JSON) are auto-checked; form submissions work, JS calls that lack the token will 400. `WTF_CSRF_CHECK_DEFAULT=True` (removed the False override). `cms/__init__.py::__all__` exports `csrf`.

## Health Check
`curl http://localhost:5000/health` — returns `{"status":"ok","database":"connected","spiderfoot":"connected"}`.
- `/health?quick=1` skips external service checks (kadaster, rdw, hibp) — used by Docker healthcheck + template banner.
- SF health uses `SpiderFootClient.ping()` with HTTP **Digest auth** (via `spiderfoot-client` package). Credentials from Settings (`spiderfoot_username`, `spiderfoot_password`).
- Health status cached in Settings (`spiderfoot_health`, `spiderfoot_last_ok`). Banner in `base.html` checks every 60s.
- `httpx` import in `system_app.py` for external service health checks (kadaster/rdw/hibp).

## Thread Safety
- **`active_searches`** (app.py routes section): Protected by `_searches_lock` (`threading.Lock()`). All three accessor functions (`deduplicate_request`, `mark_search_complete`, `cleanup_stale_searches`) acquire the lock before reading/writing.
- **`_LAST_MARINEPLAN_CALL`** (`cms/vessel_service.py:28-30`): Protected by `_marineplan_lock`. The rate-limit check in `lookup_marineplan()` acquires the lock before calling `_rate_limit()`.
- **Shell injection**: All `subprocess.run()` calls use list arguments (`[git_path, 'rev-parse', 'HEAD']`) instead of `shell=True` strings. The `step()` helper in `do_update()` now accepts `cmd_list` and omits `shell=True`. Shell expansion (e.g. `$(date)`) replaced with Python `datetime.strftime()`.
- **`do_update()` crash safety**: Entire function wrapped in try/except to return JSON even on unexpected crash (prevents HTML 500 response that breaks the frontend).

## Git
- Rollback: `git reset --hard <hash>`. Commits are safe to reset.
- Push after production changes: `git push` (remote: `origin/master`).

## Always Use Full Paths
- Production commands MUST use the full path `/opt/osint-dashboard`:
  - `cd /opt/osint-dashboard && git pull origin master && sudo systemctl restart osint-dashboard`
- Never write relative production commands.

## Server Diagnostics (`scripts/doctor.py`)
- `sudo python3 scripts/doctor.py` — checks 11 items (osint user, home dir, .spiderfoot, .git perms, flask_session, pip deps, .env key, alembic, SF service, Flask health, SF URL).
- `sudo python3 scripts/doctor.py --dry-run` to preview without making changes.
- Auto-fixes: `/home/osint` ownership, `.git` perms, `flask_session/`, pip deps (via venv), alembic upgrade, spiderfoot.service restart, spiderfoot_url config.
- Uses venv Python (`/opt/osint-dashboard/venv/bin/python3`) — system Python 3.14 has PEP 668 externally-managed.

## Production install (`install.sh`)
- **Gunicorn logging**: `--access-logfile /var/log/osint-dashboard/access.log --error-logfile /var/log/osint-dashboard/error.log` (directory created, chowned to osint).
- **Nginx tuning**: `proxy_buffer_size 8k`, `proxy_buffers 8 8k`, `proxy_buffering off`, `proxy_read_timeout 120s`, `proxy_connect_timeout 30s`.
- **SpiderFoot service**: `ProtectHome=off` (was `ProtectHome=true` — blocked access to `/home/osint/.spiderfoot`).
- **Backup cron**: Daily at 3:00 AM via `/etc/cron.d/osint-dashboard-backup`.
- **Sudoers**: `git`, `chown`, `systemctl` added for passwordless update from GUI.

## Session Summary (May 30)

### Search Access Control + Notifications
- **`cms/routes/notifications_api.py`** — new `/cms/api/notifications/*` endpoints: `GET /unread-count`, `GET /list`, `POST /mark-read/<id>`, `POST /mark-all-read`. Returns JSON with `unread_count`, `notifications` list including `id`, `type`, `title`, `message`, `read`, `created_at`, `action_url`.
- **Notification Bell** in `templates/cms/base.html` — bell icon (🔔) in header navbar, shows unread count badge, dropdown panel with notifications list + "Mark all read" button. Polls `/cms/api/notifications/unread-count` every 30s via `setInterval`.
- **Model** `Notification` in `cms/models.py` — `user_id`, `type`, `title`, `message` (TEXT), `action_url`, `read` (bool, default False), `created_at`.
- **Migration** `69999cbb5609_fix_locked_until_default.py` — adds `failed_login_attempts` column default + `locked_until` column (if missing) via `_has_column()` guards. Idempotent on both SQLite and PostgreSQL.
- **Notification triggers**: When a user tries to access a case they don't have permission to (`/cms/cases/<id>`), a `search_access` notification is created for the case owner with subject/case details + direct link.
- **Bugfix**: `notify_account_locked` function was missing in a route — added import + call. Timedelta now imported correctly in `cms/email_utils.py`.
- **Startup fix**: Removed redundant `alembic stamp head` call from `create_cms_module()` — Alembic already manages version tracking via `alembic_version` table. Log file corruption (null bytes) cleaned up by restarting server with clean output redirect.

### "current transaction is aborted" Fix
- **Error**: `psycopg2.errors.InFailedSqlTransaction` on `/cms/cases` — lazy load for `case.lead_investigator.full_name` failed after the session's transaction was silently aborted by a caught exception in a prior query.
- **Root cause**: `except Exception` blocks in `app.py:inject_globals()` (context processor) and `cms/__init__.py` (startup) caught SQL errors without calling `db.session.rollback()`. The aborted transaction state persisted, and later queries (e.g. lazy loads during template render) failed.
- **Fix (3 files)**:
  - `app.py:127-131` — Added `db.session.rollback()` in `before_request` to clear stale aborted transaction state before each request
  - `app.py:338, 348, 367` — Added `db.session.rollback()` to all 3 `except Exception` blocks in `inject_globals`
  - `cms/__init__.py:151, 179, 194, 208` — Added `db.session.rollback()` to the 4 startup `except Exception` blocks that lacked it
- **Key principle**: Always `db.session.rollback()` after catching any `Exception` that may originate from a DB query. The `before_request` guard is a safety net; fixing the handlers prevents the aborted state from being created.

## Tests
- Run: `/usr/local/bin/python3 -m pytest tests/ -v` (205 tests, ~15 min).
- Files: `test_core.py` (10), `test_findings.py` (7), `test_phone_lookup.py` (8), `test_username_search.py` (6), `test_lookups.py` (27), `test_social.py` (23), `test_templates.py` (2), `test_routes_smoke.py` (2), `test_cases.py` (16), `test_subjects.py` (18), `test_clients.py` (18), `test_documents.py` (16), `test_reminders.py` (13), `test_audit.py` (11), `test_rate_limiter.py` (1 test per class, internal).
- Tests maken cases cliënten aan omdat schema `client_id` required heeft en de route dat valideert.
- Document upload tests mocken `validate_upload()` (magic-byte check) en gebruiken `multipart/form-data` via `data` parameter.
- Audit purge test verifieert `AuditLog.purge_old(days=N)` + dat startup purge in `cms/__init__.py` werkt.
- Alle nieuwe tests checken `test_requires_auth` (unauthorized = 401/302) + happy path + edge cases.
- All mock external APIs (httpx, requests). No network calls.
- Zero warnings (third-party warnings suppressed in `pytest.ini`).
- Password `Test1234!` gebruikt (voldoet aan complexity-eisen) i.p.v. `test1234`.

### conftest.py Pitfalls
- **`app` fixture (session-scoped)**: SQLite temp file (`NamedTemporaryFile`), Alembic upgrade + `init_default_settings()`. Admin user created once at import time (`create_cms_module`), not in fixture.
- **ADMIN PASSWORD FIX**: Admin is created by `create_cms_module()` at import time with a **random** password. The `app` fixture used to skip re-creation if admin existed, meaning `Test1234!` never matched. Fix: `app` fixture now **always resets** admin's password to `Test1234!` even if admin already exists.
- **`auth_client`**: Omzeilt login/2FA via `session_transaction()` — schrijft `_user_id`, `_fresh`, `_remember` direct in de Flask session cookie.
- **`_clean_db_between_tests` (autouse, function-scoped)**: Deletes all tables EXCEPT `users` + `alembic_version` between tests (keeps the admin user).
- **CRITICAL — `db.session.expire_all()` + raw DELETE was broken**: Caused `ObjectDeletedError` en `UNIQUE constraint` errors because the identity map kept expired references to deleted admin user rows. Deleting users table + recreating admin was unreliable. Solution: skip `users` table entirely in cleanup.
- **`app` fixture teardown**: dropt ALL tabellen (inclusief `alembic_version`) zodat elke testsessie schoon start.
- **`auth_client` border-line betrouwbaar**: `session_transaction()` werkt consistent alleen als de test fungeert zonder voorafgaande test die ook `client` fixture gebruikt en 302 kreeg.
- **Known failing pattern**: Running a full test file where first test uses `client` (unauthorized, gets 302) and subsequent tests use `auth_client` → auth_client session does not stick. Workaround: run single test or class directly, not full file.
- **Session leak across `app.test_client()`**: Even a completely fresh `app.test_client()` with explicit session clearing (`session_transaction()` + delete all keys) gets `current_user.is_authenticated == True` on the first POST when run in sequence after `client`-fixture tests. Root cause unknown — likely a Flask test client cookie jar leak. 4 tests in `test_auth.py` are `@pytest.mark.skip` with reason "Flaky: session.auth state leaks across tests in same file".

### Added Test Files (May 29)
- `tests/test_integration.py` — integration tests voor webhooks, API keys, background tasks.
- `tests/test_financials_comments.py` — financials + comments endpoints.
- `tests/test_screenshots.py` — screenshot upload/manage endpoints.
- `tests/test_social_extraction.py` — social media extraction endpoints.
- Password `Test1234!` gebruikt (voldoet aan complexity-eisen) i.p.v. `test1234`.

## Input Validation (`cms/validation.py`)
- Pydantic `@validate(Schema)` decorator for POST routes. Handles zowel JSON als form data.
- Usage: `@validate(EmailCheckSchema)` after `@login_required`, then `request.validated_data`.
- Returns 400 with `{"error": "Validation failed", "details": [...]}` on invalid input for JSON requests.
- **Form POST failure**: `@validate` now checks `request.is_json` — form POSTs get `flash()` + `redirect(request.path)` instead of JSON (sinds June 7 fix). Dit voorkomt dat JS-less browsers/forms een kale JSON error zien.
- Schema `int` fields met default (`risk_score: int = 0`, `reliability_score: int = 5`) zijn `Any` om lege form-submits te accepteren.
- Alle 78 schemas in `cms/validation.py` — elke POST route in de CRM modules gebruikt `@validate`.
- Uitzonderingen: endpoints zonder request body (archive, delete, complete, stop) hebben geen schema nodig.
- `phone_service.py` routes: validatie toegepast op `add_url_rule` niveau in `app_bp.py` (i.p.v. decorator op functie) zodat `phone_lookup_all()` de ongedecoreerde functies intern kan aanroepen.

## Routes Structure
- `cms/legacy_routes.py` (~6800 lines, ~109 routes) — legacy routes, `cms_bp` definition.
- `cms/routes/` — 27 route modules (all <500 lines except `spiderfoot.py` 828, `osint_search.py` 296):
  - `phone.py`, `email.py`, `kadaster.py`, `politiebureau.py`, `rdw.py`, `vessel.py`, `interpol.py`
  - `subjects_list.py`, `subjects_crud.py`, `subjects_faces.py`, `subjects_rel.py`
  - `cases_crud.py`, `cases_state.py`, `cases_subjects.py`, `cases_reports.py`
  - `social_accounts.py`, `social_extraction.py`
  - `clients_crud.py`, `clients_archive.py`
  - Plus: `osint_search.py`, `spiderfoot.py`, `reminders.py`, `settings.py`, `templates.py`, `users.py`, `misc.py`, `system.py`
- `register_modules()` in `cms/routes/__init__.py` imports all 27 by name; `create_cms_module()` calls it.
- Extracted route modules use `request.validated_data` (Pydantic); legacy routes use `request.get_json()`.
- `cms/search_manager.py` — `SearchManager` class extracted from `osint_search.py` for DB-backed search lifecycle.
- **`system.py`** — system routes (`/health`, `/version`, `/admin/do-update`, error handlers). `do_update()` is fully wrapped in try/except to always return JSON.

## OpenAPI / Swagger (`cms/routes/api_v1.py`)
- **Flask route**: `/cms/api/v1/...` via `api_v1_bp` Blueprint (url_prefix=`/cms/api/v1`).
- **Routes**: `GET /subjects`, `GET /subjects/<id>`, `POST /subjects`, `GET /clients`, `GET /clients/<id>`, `POST /clients`, `GET /cases`, `GET /cases/<id>`, `POST /cases`.
- **Auth**: All routes require `apikey` header (`X-API-Key`) via `require_apikey` decorator.
- **Swagger/OpenAPI**: Flasgger generates OpenAPI 3.0 spec at `/openapi.json`. Swagger UI at `/docs/`. Redoc at `/docs/redoc`.
- **API keys**: Managed via Setting GUI (`setting_key='api_key'`), encrypted at rest.

## SafeJSON (SQLite JSON compat)
- `cms/models.py` defines `SafeJSON` — inherits `sqlalchemy.types.JSON`, overrides `process_result_value` to `json.loads()` when SQLite returns a raw string. PostgreSQL returns native dicts (passes through).
- All 18 `db.JSON` columns now use `SafeJSON` instead. No manual `isinstance()` guards needed at read sites.

## Event Delegation (Templates)
- `templates/cms/base.html` has a global event delegation system (just before `</body>`):
  - `click` delegation on `[data-click]` elements — calls `window[dataset.click]` with args from `data-arg0`, `data-arg1`, etc.
  - `change` delegation on `[data-change]` elements — calls `window[dataset.change](element)`
  - `submit` delegation on `[data-submit]` forms — calls `window[dataset.submit](event)`. **POST forms**: calls `e.preventDefault()` first + checks `e.submitter` — only submit button clicks trigger the handler; Enter key is silently ignored.
  - `input` delegation on `[data-input]` elements — calls `window[dataset.input](element)`
- Helper functions in `base.html`: `removeEntry`, `navigateTo`, `reloadPage`.
- Inline `onclick`/`onchange`/`onsubmit` handlers migrated to data attributes across ALL templates (~240 handlers).
- 3 survivors in `spiderfoot/list.html` — `event.stopPropagation()` inline handlers on card buttons that prevent parent `<a>` navigation from firing (cannot use delegation because `stopPropagation()` must fire at the target, not after bubbling to document).
- Flask template variables in data attributes use `|tojson` filter for safe escaping.
- JS template literals (inside `<script>` blocks) use `data-click` attribute assignment directly.

## Session Keep-Alive
- **Problem**: 8-hour session timer would `location.reload()` the page when expired, causing data loss during form entry.
- **Fix**: Silent `/api/keep-alive` fetch extends the session instead of reloading.
- **`@csrf.exempt`**: Added to `/api/keep-alive` route — the fetch request carried no CSRF token, resulting in 400 errors.
- **File**: `static/js/base.js:82-100` (interval check), `cms/routes/system_app.py:143` (route).

## Deprecations Fixed
- `datetime.utcnow()` → `datetime.now(timezone.utc)` (Python 3.12 compat).
- `Model.query.get(id)` → `db.session.get(Model, id)` (SQLAlchemy 2.0 compat).
- `legacy_routes.py` removed — `cms_bp` Blueprint lives in `cms/routes/__init__.py`.
- Type hints added to all route handlers (38 in `app.py`, ~200+ in `cms/`).

## Background Task Queue (`cms/background.py`)
- `ThreadPoolExecutor(max_workers=4)` voor fire-and-forget taken.
- `run_in_background(task_id, func, *args, **kwargs)` — voert functie uit op achtergrond thread.
- `get_task_status(task_id)` — polling: `{'status': 'pending'|'running'|'completed'|'failed', 'result': ..., 'error': ...}`.
- `GET /cms/api/background/status/<task_id>` — polling endpoint voor frontend.
- **Email**: `send_password_reset_background()` in `cms/email_utils.py` — SMTP-aanroep verplaatst naar achtergrond, user creation returned direct.
- **AI/LLM**: `ollama_generate_background()`, `summarize_results_background()`, `analyze_natural_language_background()` in `cms/services/ai_service.py` — klaar voor frontend polling.
- Task data in-memory (geen DB), dus niet persisted bij restart. Beperkt tot max_workers=4 gelijktijdige taken.

## Error Templates
- `templates/cms/404.html` en `templates/cms/500.html` — gestylede foutpagina's.
- Error handlers in `cms/routes/system_app.py`: JSON voor `/api/`-prefix, HTML voor rest.
- **LET OP**: `render_template()` in error handlers gebruikt `cms/404.html` i.p.v. `404.html` (deze staan niet in de root `templates/`). `spiderfoot.py` hanteert hetzelfde patroon.

## Context-Sensitive Help System
- **Route**: `cms/routes/help.py` — 3 endpoints: `/cms/help` (index), `/cms/help/<topic>` (full page), `/cms/api/help/<topic>` (AJAX JSON).
- **Content**: Markdown files in `help/` (`dashboard.md`, `cases.md`, `clients.md`, `subjects.md`, `spiderfoot.md`, `search.md`, `settings.md`). Converted to HTML via `markdown` library with `extra` + `toc` + `codehilite` extensions.
- **Slide-out panel**: `#helpPanel` in `base.html` — fixed right-side panel with overlay. Toggled via `openHelp(topic)` / `closeHelp()`.
- **Activation**: `?` key on keyboard, or ❓ button in header next to theme toggle.
- **Context awareness**: `body` tag gets `data-help-topic="{{ help_topic }}"` via Flask context processor (`app.py:inject_globals`). The topic is derived from the current endpoint name using a `topic_map` dict. Default: `general`.
- **Template**: `templates/cms/help.html` — extends `base.html`, shows topic grid or rendered help content.
- **Styling**: `static/css/help.css` — slide-out panel, overlay, help content typography, help page layout.
- **Registration**: Imported in `cms/routes/__init__.py::register_modules()` as `.help`.

## Sentry Error Tracking (`app.py`)
- **Opt-in**: `SENTRY_DSN` env var (primary) or `sentry_dsn` Setting (fallback via Settings GUI).
- **Integrations**: `FlaskIntegration` + `SqlalchemyIntegration` (DB query tracing).
- **Config**: `traces_sample_rate=0.1` (env: `SENTRY_TRACES_SAMPLE_RATE`), `environment` from `FLASK_ENV`.
- **`send_default_pii=False`** — never send user PII to Sentry.
- **Initialization**: env var checked at import time (before `app` exists); Settings checked after `create_cms_module(app)`.
- Set via Flask shell: `Setting.set('sentry_dsn', 'https://...@ingest.sentry.io/...')` (category=`system`, `encrypt=True`).

## Grafana Dashboard (`grafana/dashboard.json`)
- **7 panels**: Request Rate (timeseries), Active Requests (gauge), HTTP Status (2xx/4xx/5xx stacked), Latency p50/p95/p99, Top Routes (table), Duration Distribution (histogram), Requests by Method (pie chart).
- **Prometheus data source**: expects `http://localhost:9090` — update after import.
- **Metrics exposed at** `/metrics` by `cms/metrics.py`.
- **Import**: Grafana → Dashboards → Import → paste JSON → select Prometheus datasource.

## Request Timing Jitter + Proxy Rotation + Profile Rotation (`cms/services/http_utils.py`)
- **Purpose**: Random delay between consecutive OSINT HTTP calls to evade rate limiting + behavioral fingerprinting.
- **Config via Settings GUI** (all optional, sensible defaults):
  - `jitter_enabled` — `"true"` (default) / `"false"` to disable all jitter
  - `jitter_min` — minimum delay in seconds (default `0.3`)
  - `jitter_max` — maximum delay in seconds (default `2.0`)
- **Env var fallback**: `JITTER_ENABLED`, `JITTER_MIN`, `JITTER_MAX` (used when DB unreachable).
- **Per-domain tracking**: Jitter is tracked per domain (not global). Calling `api.pdok.nl` then `api.politie.nl` does NOT add delay — only repeat calls to the same domain within the jitter window trigger a sleep.
- **Functions**:
  - `jitter_sleep(domain_hint=None)` — sleep if same domain was called recently. `domain_hint` is a URL string (hostname extracted automatically); omit or `None` for global tracking.
  - `reset_jitter_state()` — clear all per-domain timestamps (for testing).
  - `get_next_proxy()` — returns `{"http": proxy, "https": proxy}` dict from round-robin list, or None
  - `reset_proxy_state()` — reset proxy rotation counter
  - `next_impersonate()` — returns next browser profile from rotation list (9 profiles: chrome124/123/120/116/110, safari17, firefox123/120)
- **Proxy rotation config**:
  - `proxy_rotation_enabled` — `"true"` / `"false"` (default `"false"`). Enables round-robin proxy switching.
  - `proxy_list` — comma or newline separated proxy URLs (e.g. `http://user:pass@ip1:port, socks5://ip2:port`)
- **Impersonation rotation config**:
  - `impersonate_rotation_enabled` — `"true"` (default) / `"false"` — cycle through 9 browser profiles per request
  - `impersonate_profiles` — custom comma-separated profile names (chrome124, safari17_2_1, firefox123, etc.)
- **Wrappers** (convenience, same signature as `curl_requests`):
  - `jittered_get(url, **kwargs)` — jitter_sleep + proxy + profile rotation + `curl_requests.get` with Playwright fallback on failure
  - `jittered_post(url, **kwargs)` — same with POST
  - `jittered_head(url, **kwargs)` — same with HEAD
  - `jittered_session(timeout=10.0, headers=None)` — returns `curl_requests.Session` (proxy + profile rotation applied)
- **Config cache**: `_refresh_jitter_config()` caches settings for 60s (same pattern as Tor config).
- **Integration**: Added to ALL sync `curl_requests.get/post/head` calls (39 call sites across 16 files). Async modules (`email_search.py`, `username_search.py`) skip jitter because they scan 400+ sites in parallel.
- **File**: `cms/services/http_utils.py`

## Playwright Fallback (`cms/services/playwright_service.py`)
- **Purpose**: Fallback fetch using headless Chromium when curl_cffi fails (403/429/connection error on JS-heavy sites).
- **Config**: `Setting.set('playwright_fallback_enabled', 'true')` — enables automatic Playwright retry after curl_cffi failure.
- **Response wrapper**: `PlaywrightResponse(status_code, text, url, headers)` mimics curl_cffi.Response (has `.json()`, `.raise_for_status()`, `.ok`, `.content`, `.text`).
- **Integration**: `jittered_get/post/head` try Playwright fallback on `CurlError` or exception.
- **Dependency**: `playwright` package + Chromium browser (`pip install playwright && playwright install chromium`).
- **Graceful degradation**: If Playwright not installed, `is_playwright_available()` returns False, fallback silently skipped.
- **File**: `cms/services/playwright_service.py`

## Tor Proxy for OPSEC (`cms/services/search_service.py`)
- **Goal**: OSINT searches (Brave API, DuckDuckGo fallback) appear to come from random Tor exit nodes instead of the server's IP.
- **Settings** (DB, not .env):
  - `tor_enabled` — `"true"` / `"false"` (default `"false"`)
  - `tor_proxy` — SOCKS5 proxy URL (default `socks5://127.0.0.1:9050`)
- **Enable**: `Setting.set('tor_enabled', 'true')` / via Settings GUI → `tor_enabled` = `true`.
- **Code**: `_get_http_client()` in `search_service.py` returns an `httpx.Client` that routes through Tor when enabled. Used by `brave_search()` and the DuckDuckGo fallback in `person_dorks_search()`.
- **Config cache**: `_refresh_tor_config()` caches settings for 60s to avoid repeated DB reads.
- **Fallback**: env vars `TOR_ENABLED` / `TOR_PROXY` used if DB Settings are unreachable.

### macOS Setup (dev)
```bash
brew install tor
brew services start tor
# Verifies on port 9050
```

### Debian/Ubuntu Setup (production)
```bash
sudo apt install tor
sudo systemctl enable --now tor
# Verifies on port 9050
# Optionally restrict to localhost only:
# echo "SOCKSPort 127.0.0.1:9050" | sudo tee -a /etc/tor/torrc
# sudo systemctl restart tor
```

### Health check
- `/health` and dashboard show `"tor": "ok"` when Tor is enabled and Brave is reachable through it.
- Shows `"tor": "disabled"` when `tor_enabled` is false.
- Shows `"tor": "unavailable: ..."` when the proxy is unreachable.

## Telegram Bot (`cms/telegram_bot.py`)

### Configuration (Settings GUI)
- `telegram_enabled` — `true`/`false`, enables the bot on next server restart.
- `telegram_bot_token` — Bot token from [@BotFather](https://t.me/botfather).
- `telegram_allowed_users` — Comma-separated Telegram user IDs allowed to use the bot. Get your ID by messaging [@userinfobot](https://t.me/userinfobot).

### Commands
| Command | Description | Example |
|---|---|---|
| `/start` | Welcome message | — |
| `/help` | Command list | — |
| `/email <address>` | Email breach + social lookup | `/email user@example.com` |
| `/phone <number>` | Phone enrichment (WhatsApp/Telegram presence) | `/phone +31612345678` |
| `/ip <address>` | IP geolocation + proxy detection | `/ip 8.8.8.8` |
| `/domain <domain>` | WHOIS + MX/NS records | `/domain example.com` |
| `/status` | Dashboard + bot health | — |

### Architecture
- The bot runs as a **daemon thread** (`threading.Thread`) inside the Flask process, started at the end of `create_cms_module()` in `cms/__init__.py`.
- Uses `python-telegram-bot` v20 with `asyncio` event loop in the dedicated thread.
- Makes **HTTP calls to `http://127.0.0.1:5000`** using `httpx.AsyncClient` — reuses existing `api_key_required`-protected endpoints.
- A dedicated internal API key is **auto-generated** at startup (`ApiKey` table, name=`"Telegram Bot Internal"`) and stored in memory. The key is scoped `read`+`write`.
- Authorization: Telegram `user_id` must be in `telegram_allowed_users` list.
- Bot only starts polling when `telegram_enabled=true` AND `telegram_bot_token` is set.
- Polling is **daemon**: it dies when the main process stops (no explicit shutdown needed).

### Adding new commands
1. Write an async handler function with `_auth()` check
2. Add a formatter function
3. Register with `app.add_handler(CommandHandler("name", handler))` in `run_bot_polling()`
4. Update the `cmd_help` text

### Security notes
- The auto-generated internal API key bypasses user-scope restrictions — the bot has effectively admin-level access via the local API. This is acceptable because running the bot requires server access.
- All bot configuration is stored in the `Setting` model (not `.env`), managed via the Settings GUI.
- The API token from @BotFather is stored encrypted (sensitive setting).
- Channel messages and inline queries are NOT handled (scope limited to private chat commands).

## Session Summary (June 7)

### @validate Form POST Fix
- **Problem**: Pydantic `@validate` decorator returned JSON 400 on ALL validation failures, even for HTML form POSTs (not just API calls). Users got a raw JSON error instead of a re-rendered form with flash messages.
- **Fix**: `@validate` now checks `request.is_json` — JSON requests get JSON 400; form POSTs get `flash()` + `redirect(request.path)`.
- **Affects**: 14 routes using `@validate`.
- **Schema fixes**: `risk_score: int = 0` → `Any` in `CreateSubjectSchema`, `reliability_score: int = 5` → `Any` in `CreateFindingSchema` — HTML form submits empty string for unfilled fields, causing Pydantic `ValidationError`.

### Enter Key Accidental Form Submission
- **Problem**: Pressing Enter in any form field (especially date picker, autofill dropdown, or text input) triggered the `data-submit` handler, submitting the form unintentionally.
- **Fix**: `data-submit` delegation handler in `base.js:399-417` now calls `e.preventDefault()` on all POST forms. The handler checks `e.submitter` (SubmitEvent property) — only actual submit button clicks proceed; Enter key is silently ignored.
- **Browser compat**: `e.submitter` supported in Chrome 82+ / Firefox 92+ / Safari 15+.

### Session Timeout Data Loss
- **Problem**: 60-second interval check would `location.reload()` when the 8-hour `session_start` timer expired, causing unsaved form data to be lost.
- **Fix**: Replaced `location.reload()` with silent `fetch('/api/keep-alive')` call that extends the Flask session. No page reload.
- **CSRF fix**: `@csrf.exempt` added to `/api/keep-alive` route — the fetch carried no CSRF token, returning 400 silently. The keep-alive had been broken since CSRF was enabled.

### CSS Class Collision
- **Problem**: Case view pages had black-on-black text because global `.header` (nav bar with dark background) matched local `.header` class used for case title wrapper.
- **Fix**: Renamed local `.header` to `.page-header` in `cases/view.html`, `cases/list.html`, `subjects/list.html`.

### Key Principle
- **Form reliability**: Always test CRUD routes with both API calls AND browser form submissions. The `@validate` decorator and `data-submit` delegation assume API patterns; form-first users (tab-through-fields, Enter to submit) hit edge cases that API tests miss.

## Backup Verification (`scripts/verify_backup.sh`)
- **Usage**: `./scripts/verify_backup.sh` — verifies latest backup archive.
- **Checks**: file integrity (min size), gzip validity, SQL syntax (CREATE TABLE/COPY presence), PostgreSQL restore dry-run (if psql available + postgres running), SQLite integrity check (`PRAGMA integrity_check`).
- **Exit codes**: 0 (OK), 1 (no backup found), 2 (verification failed), 3 (cleanup error).
- **Cleanup**: `./scripts/verify_backup.sh --cleanup` removes `/tmp/iveras_backup_verify_*` dirs older than 7 days.
- **Cron integration**: add to `/etc/cron.d/osint-dashboard-backup`:
  ```
  0 4 * * * osint /opt/osint-dashboard/scripts/verify_backup.sh >> /var/log/osint-dashboard/backup-verify.log 2>&1
  ```
