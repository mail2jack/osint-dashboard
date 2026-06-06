# Changelog

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
