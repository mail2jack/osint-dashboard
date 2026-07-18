# Changelog

## [3.7.1] — 2026-07-18

### Fixed
- Instagram email check false positives (direct Instagram probe i.p.v. imginn.com)
- WhatsApp interne presence check (default `exists = True` → pattern-based)
- Telegram interne presence check (status 400/200 → absence/presence patterns)
- ScrapTik API response structuur (data→user, uniqueId→unique_id, etc.)
- PV regenerate overschrijft niet langer hele body (alleen findings-samenvatting via markers)
- Settings OPSEC/Tor save-knop zat in verkeerde `<form>` (dubbel form#settingsForm)
- `_check_tor_config()` was destructief (zette tor_enabled hard op false)

### Changed
- **Update flow naar async** (background thread + polling) — voorkomt 504 door nginx timeout
- PV body HTML escaping gefixt (`{{ body_html|safe }}`)
- OPSEC dashboard toont nu effectieve runtime-waarden i.p.v. `—` voor settings zonder DB row
- PATH fix voor backup scripts onder systeemd (dirname/date找不到)
- Changelog/footer link fix voor version.py
- Async update: PermissionError op chmod afgevangen, Task None op startup schrijft error state,
  abort edge cases afgevangen, exception traceback in error output, JSON errors voor `/cms/admin/` endpoints
- VERSION bumped naar 3.7.1

### Added
- PV screenshot thumbnails (max 240x120, klikbaar)
- Archief-filter (gearchiveerde findings/actions hidden by default)
- "📝 Handmatige invoer" ResearchAction (`manual_entry` actietype)
- API endpoint `POST /api/case/<case_id>/manual-finding`
- Rollback endpoint (`POST /admin/rollback-update`)
- Update UI modal met dynamische knoppen (Afbreken/Rollback/Opnieuw), 7 UI modes
- Markdown cheat sheet in PV-editor
- `"opsec"` categorie in Settings sidebar (Tor settings nu bewerkbaar)

## [3.7.0] — 2026-07-02

### Added
- `README.md` and `INSTALL.md` documentation
- `requirements-dev.txt` for test/lint tools (separate from production deps)
- Python 3.12+ version check in `install.sh` with deadsnakes PPA auto-install
- NodeSource 22.x setup in `install.sh` (replaces outdated Ubuntu nodejs)
- `playwright install chromium` in `install.sh`
- `osint-bot.service` installation (if present in `deploy/`)
- Default password check, `FLASK_ENV`, and `DB_SSL_MODE` checks in `scripts/doctor.py`

### Changed
- `install.sh` branch: `master` → `saas-migration`
- `update.sh` uses dynamic current branch instead of hardcoded `master`
- Generated `.env` now includes `FLASK_ENV=production` and `DB_SSL_MODE=prefer`
- Gunicorn timeout increased 120s → 300s for slow first boot
- `2>/dev/null` removed from npm build commands (errors are now visible)
- `.env.example` now includes `FLASK_ENV`, `DB_SSL_MODE`, uncommented `DATABASE_URL`
- CI workflows use `requirements-dev.txt` for test/lint deps
- MANUAL.md installation section updated (branch, Python 3.12+, NodeSource, Playwright, full steps)

### Fixed
- Missing `print_warning` function in `install.sh` (caused bash error on SSL failure)
- Certbot email now prompted with validation instead of hardcoded `admin@$DOMAIN`

## [3.6.0] — 2026-06-06

### Added
- Extern JavaScript bestand (`static/js/base.js`) — 541 regels inline JS uit base.html geëxtraheerd met cache-busting
- Cache-busting voor `cms-professional.css` en `help.css` via `?v={{ g.css_version }}`
- `cached_setting_get()` toegepast in `spiderfoot.py` en `ai_service.py` — Settings-cache met 60s TTL

### Changed
- **Dashboard**: 6 aparte COUNT queries → 1 GROUP BY query
- **Statistics**: 9 aparte COUNT queries → 2 GROUP BY queries
- **Reminders**: 3 aparte COUNT queries → 1 aggregated query met FILTER (WHERE ...)
- **FTS search**: N+1 per-subject `case_subjects` queries → 1 bulk query
- Inline `window.CMS` config-script voor Jinja2-variabelen (CSRF, locale, counts, etc.)

### Fixed
- 500 error na `git pull` door verouderde `.pyc` cache — restart of `__pycache__` wissen nodig

## [3.5.0] — 2026-05-20

### Added
- WhatsApp/Telegram presence check via `whatsapp.checkleaked.cc` API (RapidAPI)
- Maandelijkse usage teller (50 req/maand) met visuele indicator in popup
- Val terug op scraping (`api.whatsapp.com/send` + `t.me`) bij API-storing of limiet

### Fixed
- `date_of_birth`, `place_of_birth`, `identification_number` werden niet opgeslagen bij create subject (wel bij edit) — toegevoegd aan Subject constructor in create route
- `identification_number` werd niet getoond op subject detailpagina — weergave toegevoegd in person blok
- `owner_name`/`owner_address` waren dode velden (geen DB kolommen) — verwijderd uit create form
- Cases OSINT resultaten waren onzichtbaar door checkbox CSS uit base template — `width: auto; padding: 0; border: none` override op `.result-item input[type="checkbox"]`
- Subject view OSINT modal zelfde checkbox fix
- Dark mode OSINT resultaten gefixt (wit-op-wit na base template :root blok)
- `rdw_fields` gesynchroniseerd tussen create/edit routes

### Changed
- Comments: sectie verplaatst naar tussen Subject Details en Social Media IDs
- Subject.notes gemigreerd naar Comment model
- OSINT scan resultaten: URL dedup (geen harde UNIQUE, <60s filter)
- Telefoonnummers worden nu automatisch genormaliseerd naar E164 (+31634407404) bij create/edit
- PhoneLookup model: opgeslagen checks tonen bij herbezoek, refresh knop voor nieuwe API call

## [3.4.2] — Before 2026-05-19

- Eerdere versies, zie git history.
