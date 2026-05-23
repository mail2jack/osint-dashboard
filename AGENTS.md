# Iveras OSINT Dashboard — Agent Guide

## Entrypoint & Run
- `app.py` is the single Flask entrypoint. Dev: `/usr/local/bin/python3 app.py` (port 5000). **Python 3.12 required** — `python3` must resolve to `/usr/local/bin/python3` or use the full path.
- CMS module initialized via `cms/__init__.py::create_cms_module(app)`.
- Dev with SpiderFoot: `./start.sh start`. Stops: `./start.sh stop`.
- Production: `sudo ./install.sh` (Debian/Ubuntu — sets up Nginx, PostgreSQL, SpiderFoot, systemd, SSL). Accepts space-separated domain list for multi-domain SSL.

## Database
- `install.sh` sets up PostgreSQL by default (`DATABASE_URL` in `.env`). Falls back to SQLite (`cms.db`) if `DATABASE_URL` is unset.
- Local dev uses PostgreSQL too. Setup: `brew install postgresql@16 && brew services start postgresql@16 && createdb cms_dev && echo 'DATABASE_URL=postgresql:///cms_dev' >> .env && pip install psycopg2-binary`.
- `db.create_all()` runs on first startup — tables + default admin (`admin`/`changeme123`) auto-created.
- Never mutate `created_at` on ORM objects directly (crashes SQLite). Sort with `strftime()` in sort key lambda.
- `instr()` in queries is dialect-agnostic (uses `instr` for SQLite, `strpos` for PostgreSQL) — see `cms/routes.py`.
- **CRITICAL — PostgreSQL vs SQLite diff**: PostgreSQL enforces `VARCHAR(n)` length limits; SQLite ignores them. Fernet-encrypted values are ~100-140 chars, so ALL encrypted columns MUST be `String(500)` minimum. The app auto-migrates column sizes on startup via `ALTER COLUMN TYPE VARCHAR(500)` (PostgreSQL only, see `cms/__init__.py`).

## SpiderFoot Integration (`cms/spiderfoot_service.py`)

### Config source (critical)
- SpiderFoot config is read from the `Setting` model (DB table), NOT from `.env`.
- `get_spiderfoot_config()` in `routes.py:5406` calls `Setting.get('spiderfoot_url')`, `Setting.get('spiderfoot_password')`, etc.
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

## Phone Lookup (`routes.py:phone_lookup`)
- `POST /cms/api/phone-lookup` — validates + enriches phone numbers using `phonenumbers` library + free `bedrijfsdata.nl` API (NL only).
- Returns: valid, formatted, country, region, carrier, line_type, timezone, WhatsApp/Telegram presence.
- Button "📞 Check" appears next to phone fields in subject/client view pages (hidden if no phone).
- Requires `phonenumbers` (`pip install phonenumbers`).
- Depends on `httpx` (already in requirements).
- `re` imported globally in routes.py for number normalization.
- **`normalize_phone()`** (routes.py:6307) — normaliseert elk telefoonformaat naar E164 (`+31634407404`). Wordt aangeroepen bij create/edit van subject + client (zowel los veld als contacts).
- **WhatsApp/Telegram check**: Uses `whatsapp.checkleaked.cc` API (RapidAPI) when `whatsapp_checkleaked_key` Setting is set. Falls back to unreliable scraping (`api.whatsapp.com/send` + `t.me`) when API returns 503 or no key configured.
- API key: `Setting.set('whatsapp_checkleaked_key', 'your-rapidapi-key')` (BASIC free tier: 50 req/month). Signs up at whatsapp.checkleaked.cc/pricing.
- API response includes `isWAContact`/`isUser` for WhatsApp presence + `telegram` object with existence check.
- API response included in popup: about, business, enterprise, verified, banned, line_type, cached status, check date.
- Profielfoto wordt automatisch gefetcht en als base64 weergegeven (indien beschikbaar).
- Alle API responses worden opgeslagen in `PhoneLookup` model (tabel `phone_lookups`) met timestamp + raw JSON + profielfoto.

## Interpol + Politie Check (`routes.py:check_policie_data`)
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

## Politiebureau Lookup (`routes.py:politiebureau_lookup`)
- `POST /cms/api/politiebureau-lookup` — finds nearest police station for an address.
- Accepts `{address_id}` (resolves from DB) or `{lat, lon}` directly.
- Uses coordinates from `kadaster_data` (if stored) or falls back to PDOK BAG lookup, then calls `api.politie.nl/politiebureaus/v1`.
- Returns: station name, address, phone, opening hours, OSM map link, politie.nl page URL.
- **Button** "🚔 Politiebureau" on each address card in subject view page (`view.html`), next to the Kadaster button.
- Result displayed in a red-themed card below the address.

## Vessel / Ship Lookup (`cms/vessel_service.py`, `routes.py:vessel_lookup`)
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
- Auto-migration in `cms/__init__.py` (ALTER TABLE ADD COLUMN) for existing DBs.
- New encrypted cols also listed in the ALTER COLUMN resize migration for PostgreSQL.

## API Keys — Settings GUI (not .env)
- Prefer Settings table over `.env` for API keys. Use `Setting.set('key_name', 'value')` via Flask shell or the Settings GUI.
- Getter functions in `app.py`: `_get_overheid_key()`, `_get_twochat_credentials()`, `_get_brave_key()`. Pattern: env var override → Setting fallback.
- Hardcoded `OVERHEID_API_KEY`, `TWOCHAT_API_KEY`, `TWOCHAT_WHATSAPP_NUMBER` at module level are deprecated — kept for backward compat but no longer used in routes (routes call getter functions instead).
- One-time migration script `scripts/migrate_env_to_settings.py` copies existing `.env` values to DB.
- Twitter Basic Auth (`read:api` at `app.py:1051`) is dead (Twitter v1.1 deprecated), ignore.
- `.env` should only retain `DATABASE_URL`, `CMS_ENCRYPTION_KEY`, `FLASK_SECRET_KEY`. Move all API keys to Settings.

## Update Notifications
- `check_update()` at `routes.py:5175` checks both VERSION file AND latest commit SHA from GitHub.
- Banner shows for version bumps OR any new commits (bugfixes without version change).
- `last_update_commit` Setting stores the local HEAD SHA after each successful `do_update()`.
- If remote SHA differs from stored SHA, a "New commits available" notification appears.
- **CRITICAL — `update_check_repo` must be set** in the DB (`Setting.set('update_check_repo', 'mail2jack/osint-dashboard')`). Without it, the API returns `check_enabled: False` and the banner NEVER shows. `install.sh` sets this automatically; manual setups MUST set it.
- **Auto-detect**: If `last_update_commit` is empty, `check_update()` runs `git rev-parse HEAD` and stores the result. This means the banner can only detect commits pushed AFTER the first page visit — visiting AFTER a manual `git pull` will silently store the new HEAD and show no diff.
- **Diagnostic**: Run `sudo -u osint /opt/osint-dashboard/venv/bin/python -c "from app import app; from cms.models import Setting; app.app_context().push(); print('repo:', Setting.get('update_check_repo')); print('last_sha:', Setting.get('last_update_commit','(empty)'))"`

## Health Check
`curl http://localhost:5000/health` — returns `{"status":"ok","database":"connected","spiderfoot":"connected"}`.

## Git
- Rollback: `git reset --hard <hash>`. Commits are safe to reset.
- Push after production changes: `git push` (remote: `origin/master`).

## Always Use Full Paths
- Production commands MUST use the full path `/opt/osint-dashboard`:
  - `cd /opt/osint-dashboard && git pull origin master && sudo systemctl restart osint-dashboard`
- Never write relative production commands.

## Tests
- Run: `/usr/local/bin/python3 -m pytest tests/ -v` (58 tests, ~2-3 min).
- Files: `test_core.py` (10), `test_findings.py` (7), `test_phone_lookup.py` (8), `test_username_search.py` (6), `test_lookups.py` (27).
- All mock external APIs (httpx, requests). No network calls.
- `conftest.py`: SQLite temp file, `auth_client` via `session_transaction()` (omzeilt 2FA), `db_session`.
- 37 third-party warnings remain (flask_login + flask_sqlalchemy internals).

## Input Validation (`cms/validation.py`)
- Pydantic `@validate(Schema)` decorator for POST routes.
- Usage: `@validate(EmailCheckSchema)` after `@login_required`, then `request.validated_data`.
- Returns 400 with `{"error": "Validation failed", "details": [...]}` on invalid input.
- Schemas available for all lookups.py + social.py routes.

## Routes Structure
- `cms/legacy_routes.py` (~6819 lines, ~109 routes) — legacy routes, `cms_bp` definition.
- `cms/routes/lookups.py` (13 routes) — phone, email, kadaster, politiebureau, RDW, vessel, Interpol/politie.
- `cms/routes/social.py` (8 routes) — social account CRUD, username findings, social ID extraction.
- Extracted routes use `request.validated_data`; legacy routes use `request.get_json()`.
- `cms/routes/__init__.py` re-exports `cms_bp`; `cms/__init__.py::create_cms_module()` calls `register_modules()`.

## Deprecations Fixed
- `datetime.utcnow()` → `datetime.now(timezone.utc)` (Python 3.12 compat).
- `Model.query.get(id)` → `db.session.get(Model, id)` (SQLAlchemy 2.0 compat).
