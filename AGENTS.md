# Iveras OSINT Dashboard — Agent Guide

## Entrypoint & Run
- `app.py` is the single Flask entrypoint. Dev: `python app.py` (port 5000).
- CMS module initialized via `cms/__init__.py::create_cms_module(app)`.
- Dev with SpiderFoot: `./start.sh start`. Stops: `./start.sh stop`.
- Production: `sudo ./install.sh` (Debian/Ubuntu — sets up Nginx, PostgreSQL, SpiderFoot, systemd, SSL).

## Database
- Default: SQLite at project root `cms.db`. PostgreSQL: set `DATABASE_URL` in `.env`.
- `db.create_all()` runs on first startup — tables + default admin (`admin`/`changeme123`) auto-created.
- Never mutate `created_at` on ORM objects directly (crashes SQLite). Sort with `strftime()` in sort key lambda.

## SpiderFoot Integration (`cms/spiderfoot_service.py`)

### Config source (critical)
- SpiderFoot config is read from the `Setting` model (DB table), NOT from `.env`.
- `get_spiderfoot_config()` in `routes.py:5406` calls `Setting.get('spiderfoot_url')`, `Setting.get('spiderfoot_password')`, etc.
- Setting values via Flask shell: `Setting.set('spiderfoot_url', 'http://...')`.

### Auth
- SpiderFoot v4 uses HTTP Digest auth. Credentials stored in `~/.spiderfoot/passwd` (`admin:<password>`).
- Start with auth: `python3 sf.py -l 127.0.0.1:5001 --passwd ~/.spiderfoot/passwd`.

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

## Interpol + Politie Check (`routes.py:check_policie_data`)
- `POST /cms/check-policie-data` — checks subject name against INTERPOL Red Notices (wanted) + Yellow Notices (missing) + politie.nl/vermist (NL missing persons).
- **Button** "🌍 Check Interpol" on subject view page (was "🚔 Check Politie Data").
- Interpol API: `ws-public.interpol.int` (Akamai rate-limited, may return 403 after many calls).
- Fallback: scrapes `politie.nl/vermist` for matching names when Interpol returns no results.
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

## Testing
```bash
python tests/test_core.py
```
One test file: `tests/test_core.py` (email, IP, domain validation; phone normalization). Uses pytest.

## Health Check
`curl http://localhost:5000/health` — returns `{"status":"ok","database":"connected","spiderfoot":"connected"}`.

## Git
- Rollback: `git reset --hard <hash>`. Commits are safe to reset.
- Push after production changes: `git push` (remote: `origin/master`).
