# Iveras OSINT Dashboard — Agent Guide

## Entrypoint & Run
- `app.py` is the single Flask entrypoint. Dev: `/usr/local/bin/python3 app.py` (port 5000). **Python 3.12 required** — `python3` must resolve to `/usr/local/bin/python3` or use the full path.
- CMS module initialized via `cms/__init__.py::create_cms_module(app)`.
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
- `cms/services/ai_service.py`: `OLLAMA_URL`/`OLLAMA_MODEL` defined once here. `get_ollama_config()` reads Setting/env/hardcoded fallback. `app.py` imports from here — no duplicate constants.

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

## Tests
- Run: `/usr/local/bin/python3 -m pytest tests/ -v` (205 tests, ~15 min).
- Files: `test_core.py` (10), `test_findings.py` (7), `test_phone_lookup.py` (8), `test_username_search.py` (6), `test_lookups.py` (27), `test_social.py` (23), `test_templates.py` (2), `test_routes_smoke.py` (2), `test_cases.py` (16), `test_subjects.py` (18), `test_clients.py` (18), `test_documents.py` (16), `test_reminders.py` (13), `test_audit.py` (11), `test_rate_limiter.py` (1 test per class, internal).
- Tests maken cases cliënten aan omdat schema `client_id` required heeft en de route dat valideert.
- Document upload tests mocken `validate_upload()` (magic-byte check) en gebruiken `multipart/form-data` via `data` parameter.
- Audit purge test verifieert `AuditLog.purge_old(days=N)` + dat startup purge in `cms/__init__.py` werkt.
- Alle nieuwe tests checken `test_requires_auth` (unauthorized = 401/302) + happy path + edge cases.
- Pauze tussen testfiles wordt veroorzaakt door `app` fixture (function scope): elke testfile hercreëert de hele schema via Alembic.
- `test_lookups.py` (27 tests, ~96s) traag door setup/teardown overhead per test (mock patches op httpx/requests).
- All mock external APIs (httpx, requests). No network calls.
- `conftest.py`: SQLite temp file, `auth_client` via `session_transaction()` (omzeilt 2FA), `db_session`.
- Schema via Alembic (`alembic upgrade head` in fixture setup, niet `db.create_all()`).
- Teardown dropt ALL tabellen (inclusief `alembic_version`) zodat elke test schoon start.
- Zero warnings (third-party warnings suppressed in `pytest.ini`).

## Input Validation (`cms/validation.py`)
- Pydantic `@validate(Schema)` decorator for POST routes. Handles zowel JSON als form data.
- Usage: `@validate(EmailCheckSchema)` after `@login_required`, then `request.validated_data`.
- Returns 400 with `{"error": "Validation failed", "details": [...]}` on invalid input.
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

## SafeJSON (SQLite JSON compat)
- `cms/models.py` defines `SafeJSON` — inherits `sqlalchemy.types.JSON`, overrides `process_result_value` to `json.loads()` when SQLite returns a raw string. PostgreSQL returns native dicts (passes through).
- All 18 `db.JSON` columns now use `SafeJSON` instead. No manual `isinstance()` guards needed at read sites.

## Event Delegation (Templates)
- `templates/cms/base.html` has a global event delegation system (just before `</body>`):
  - `click` delegation on `[data-click]` elements — calls `window[dataset.click]` with args from `data-arg0`, `data-arg1`, etc.
  - `change` delegation on `[data-change]` elements — calls `window[dataset.change](element)`
  - `submit` delegation on `[data-submit]` forms — calls `window[dataset.submit](event)`
  - `input` delegation on `[data-input]` elements — calls `window[dataset.input](element)`
- Helper functions in `base.html`: `removeEntry`, `navigateTo`, `reloadPage`.
- Inline `onclick`/`onchange`/`onsubmit` handlers migrated to data attributes across ALL templates (~240 handlers).
- 3 survivors in `spiderfoot/list.html` — `event.stopPropagation()` inline handlers on card buttons that prevent parent `<a>` navigation from firing (cannot use delegation because `stopPropagation()` must fire at the target, not after bubbling to document).
- Flask template variables in data attributes use `|tojson` filter for safe escaping.
- JS template literals (inside `<script>` blocks) use `data-click` attribute assignment directly.

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
