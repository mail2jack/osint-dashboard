# External Integrations

## SpiderFoot Integration (`cms/spiderfoot_service.py`)

### Config source (critical)
- Config read from `Setting` model (DB), NOT from `.env`.
- `get_spiderfoot_config()` calls `Setting.get('spiderfoot_url')`, `Setting.get('spiderfoot_password')`, etc.
- Setting values via Flask shell: `Setting.set('spiderfoot_url', 'http://...')`.
- **GUI**: SpiderFoot config now available via Settings → 🕷️ SpiderFoot category (sidebar, Integrations group).

### Auth
- SpiderFoot v4 uses HTTP Digest auth. Credentials stored in `~/.spiderfoot/passwd`.
- Start with auth: `python3 sf.py -l 127.0.0.1:5001`.

### API data quirks
- **Scan list format**: `[id, name, target, created, started, completed, status, resultCount, riskSummary]` — status is UPPERCASE.
- **Result format**: `[timestamp, data, value, sourceModule, ..., type]`.
- **SFURL tags**: Result `data` contains HTML-escaped `<SFURL>` tags. Must `html.unescape()` before regex parsing (done in `normalize_result()`).

### Templates
- Live in `templates/cms/spiderfoot/`: `index.html`, `view.html`, `scan.html`, `list.html`, `scan_subject.html`.
- Template filters in `app.py:103-270`: `urlize_target`, `result_link`, `platform_name`, `platform_color`.
- Rich result cards use `.rich-card` with `--card-color` CSS custom property.

---

## Email & AI Config
- `cms/email_utils.py`: SMTP with `ssl.create_default_context()` (TLS cert verification).
- `cms/services/ai_service.py`: Dual-provider — **OpenRouter** (primary) + **Ollama** (fallback). `_generate()` tries OpenRouter first; falls back to Ollama on failure.
- Set OpenRouter API key via Settings → API Keys → `openrouter_api_key`, or env `OPENROUTER_API_KEY`.
- Model selection at Settings → AI Provider → `openrouter_model` (default: `openrouter/auto`).
- Consumer functions: `summarize_results`, `analyze_natural_language`, `enrich_profile`.

---

## Phone Lookup (`cms/routes/phone.py`)
- `POST /cms/api/phone-lookup` — validates + enriches using `phonenumbers` + `bedrijfsdata.nl` API (NL only).
- Returns: valid, formatted, country, region, carrier, line_type, timezone, WhatsApp/Telegram presence.
- **`normalize_phone()`** normalizes to E164 — called on subject/client create/edit.
- **WhatsApp/Telegram check**: Uses `whatsapp.checkleaked.cc` API (RapidAPI key via `Setting.set('whatsapp_checkleaked_key', ...)`).
- All API responses stored in `PhoneLookup` model with timestamp + raw JSON + profile photo.

---

## Interpol + Politie Check (`cms/routes/interpol.py` + `cms/politie_scraper.py`)
- `POST /cms/check-policie-data` — checks subject name against INTERPOL Red/Yellow Notices + politie.nl/vermist + politie.nl/gezocht.
- **Button** "🌍 Check Interpol" on subject view page.
- Interpol API: `ws-public.interpol.int` (Akamai rate-limited).
- Politie scraper extracts Nuxt SSR payload, resolves reactive refs.
- Status check: `GET /cms/check-policie-data-status`.

---

## Address Form — Postcode Check
- **🔍 button** next to zipcode: calls `POST /cms/api/kadaster-lookup` with `{zipcode, number}` → fills in street + town from PDOK BAG.
- JS functions: `postcodeCheck(btn)` in `create.html` and `edit.html`.
- `serializeAddresses()` includes `number` field.

---

## Politiebureau Lookup (`cms/routes/politiebureau.py`)
- `POST /cms/api/politiebureau-lookup` — finds nearest police station for an address.
- Accepts `{address_id}` or `{lat, lon}`.
- Uses PDOK BAG → `api.politie.nl/politiebureaus/v1`.
- **Button** "🚔 Politiebureau" on each address card.

---

## Vessel / Ship Lookup (`cms/vessel_service.py`, `cms/routes/vessel.py`)
- `POST /cms/api/vessel-lookup` — searches VesselFinder, MarinePlan, KVNR Schepenzoeker, Binnenvaart.eu, Equasis.
- `POST /cms/api/vessel/update-subject` — updates subject with vessel data.
- `POST /cms/api/findings/from-vessel` — creates Finding from vessel data.
- Vessel fields (IMO, MMSI, ENI, flag) encrypted via Fernet.
- MarinePlan key: `Setting.set('marineplan_api_key', '...')`.
- Equasis: `Setting.set('equasis_email', '...')` + `Setting.set('equasis_password', '...')`.
- `lookup_marineplan()` rate-limited (2s between calls).
- KVNR and Binnenvaart.eu are public scrapes.
- **DB Migration**: `subjects` table columns `imo_number`, `mmsi`, `eni_number`, `vessel_nationality` (all `String(500)`), `vessel_data` (TEXT).

---

## Telegram Bot (`cms/telegram_bot.py`)
- Settings: `telegram_enabled`, `telegram_bot_token`, `telegram_allowed_users`.
- Commands: `/start`, `/help`, `/email`, `/phone`, `/ip`, `/domain`, `/status`.
- Runs as daemon thread inside Flask process, started at end of `create_cms_module()`.
- Uses `python-telegram-bot` v20 with asyncio in dedicated thread.
- Makes HTTP calls to `http://127.0.0.1:5000` using internal auto-generated API key.
- Adding commands: write async handler with `_auth()` check, add formatter, register with `app.add_handler()`.

---

## Sherlock Username Search (`cms/sherlock_utils.py`, `cms/username_search.py`)
- Sherlock site list wordt live opgehaald van `https://raw.githubusercontent.com/sherlock-project/sherlock/master/sherlock_project/resources/data.json` en 24u gecached in `instance/sherlock_cache.json`.
- `get_sherlock_sites()` in `cms/sherlock_utils.py` cached via `@lru_cache` + disk cache met TTL.
- `search_username_async(username, max_sites=150)` in `cms/username_search.py` checkt sites uit de Sherlock JSON. `PRIORITY_USERNAME_SITES` (24 stuks) worden eerst gecheckt.
- **Uitbreiden mogelijkheden:**
  1. **Meer sites**: verhoog `max_sites` (default 150) — Sherlock heeft ~500+ sites.
  2. **Prioriteit**: voeg namen toe aan `PRIORITY_USERNAME_SITES` (moeten exact matchen met keys in upstream `data.json`).
  3. **Eigen sites**: merge na het ophalen in `get_sherlock_sites()` extra dict-entries — formaat: `{"errorType": "status_code", "url": "https://site.nl/{}", "urlMain": "https://site.nl", "username_claimed": "test"}`.
- Hoe meer sites, hoe langer de check duurt (`batch_size=30` parallel).
- Email search (`cms/email_search.py`) gebruikt dezelfde Sherlock-data voor email-checks met eigen `priority_sites` (30 stuks).
- Vallet altijd terug op Sherlock als RapidAPI onconfigureerd/uitgeput is (`cms/routes/osint_routes.py:1045`).

---
- Prefer Settings table over `.env` for all API keys.
- API key access pattern: `Setting.get('openrouter_api_key')`, `Setting.get('overheid_api_key')`, etc. — env var fallback removed; use Settings GUI.
- `.env` should only retain `DATABASE_URL`, `CMS_ENCRYPTION_KEY`, `SECRET_KEY`.
- One-time migration script: `scripts/migrate_env_to_settings.py`.
