# Session Summaries / Changelog

## August 12 — Beveiligingssprint afgerond + VPS-deploy + veilig deploy-script

### Beveiliging
- `cms/auth.py::ensure_case_access()` (tenant + `can_access_case`, audit-403); alle workflow-routes incl. archive/restore van actions/findings resolven naar de parent case met fallback `ensure_tenant_access`.
- `cms/config.py` ProductionConfig: harde TLS-floor `require`; `verify-ca`/`verify-full` worden nu ook echt toegepast op `DB_SSL_MODE` + engine `connect_args` (niet meer alleen geaccepteerd).
- `license-server/app.py`: `LICENSE_ENV`-marker + `LICENSE_ADMIN_SECRET` fail-fast in productie; `deploy/license-server.service` zet `Environment=LICENSE_ENV=production`.
- `scripts/doctor.py`: schrijft bij ontbrekende/te zwakke `DB_SSL_MODE` nu `require`.
- `requirements-lock.txt`: `backports.zstd==1.6.0` verwijderd (geen Python 3.14-wheel).

### Deploy (VPS, 12 aug)
- Dashboard: `DB_SSL_MODE=prefer`→`require` in `.env`; deps op Python 3.14; doctor 21/21; pip-audit 0.
- License-server: fail-fast bewezen (RuntimeError bij kapotte postgres; ValueError bij `prefer`/zonder secret).
- **Incident**: `rsync --delete` zonder `venv/`-exclude wist `/opt/license-server/venv/` (en `.gunicorn/`); `data/`/`keys/`/`.env` overleefden dankzij excludes. Hersteld: `LICENSE_ADMIN_SECRET` toegevoegd, venv herbouwd, service herstart, `/health` ok.

### Wijzigingen
- `license-server/deploy/deploy.sh` (nieuw): veilige rsync met excludes (.env, venv/, data/, keys/, .cache/, .gunicorn/, __pycache__/) + zelf-herstellende venv-herbouw + unit/daemon-reload/restart/health check. README-deploy-blok verwijst nu hiernaartoe.

## August 2 — Licenties fase 2: Ed25519 + soft trial (per install)

### Doel
Fase 1 (telemetrie + registry, live op prod) uitbouwen tot een echt
licentiesysteem: Ed25519-ondertekende licenties die de app offline verifieert,
een automatische trial bij registratie, en soft-trial-limieten (tenants,
externe integraties, AI) per install.

### Wijzigingen (license-server)
- **`licensing.py`**: Ed25519 via `cryptography`; `canonical_payload`,
  `sign_claims`, `build_license`, `generate_keypair` (privésleutel →
  `keys/private.pem`, mode 600), `load_private_key`. Sleutelpad nu via
  `LICENSE_KEY_PATH` env (default `keys/private.pem`).
- **`cli.py`**: `keys:generate`, `license:new --install <id> [--plan full|trial]
  [--expires YYYY-MM-DD | --days N]`, `license:revoke --install`, `license:list`.
- **`app.py`**: `licenses`-tabel (UNIQUE install_id); `_issue_trial_if_needed`
  (env `TRIAL_DAYS`, default 30) bij registratie; register/telemetry-respons
  bevatten `license`; nieuw `GET /api/license` (Bearer + `X-Install-ID`;
  401/403/404); `/api/installs` incl. licentie per install; dashboard
  License-kolom (plan/status/expires, kleur-dot).
- **`tests/test_server.py`** (nieuw, 20 tests): register→auto-trial,
  signature-verificatie, idempotent/wrong-token/413, `/api/license`
  (signed/unregistered/wrong-token/missing-auth/revoked), telemetry
  (incl. license + 404), CLI als subprocess (new vervangt trial, unregistered
  install, revoke, list), dashboard basic-auth + installs. De server-app wordt
  via importlib geladen om botsing met de root-`app` te vermijden.
- **`.gitignore`**: `license-server/keys/`, `license-server/data/`,
  `license-server/.env`.

### Wijzigingen (dashboard client)
- **`cms/services/license.py`** (nieuw): `verify_signature`, `cache_license`
  (weigert ongeldige signatures), `get_license_state` (toestandsmachine:
  revoked/expired/≤14d-waarschuwing), `is_licensed`, `enforcement_off`
  (`LICENSE_ENFORCEMENT=off`), `trial_mode`, `trial_blocked(feature)`
  (gated: `ai`, `spiderfoot`, `vessel`, `phone`), `trial_tenant_limit`
  (default 1), `get_public_key`. Default publieke sleutel ingebakken:
  `4xvSvYw1F9tjTfss0e_6XpdUnPxiOaFdK0shP3cxz-U` (huidige prod-keypair; eerdere
  sleutels `MiZPC_…` hadden geen privésleutel, `0O8J…` werd verloren door een
  deploy-`rsync --delete` zonder `keys/`-exclude — inmiddels gefixt).
- **`cms/services/telemetry.py`**: `_send` retourneert Response; `_consume_license`
  cachet de licentie bij register/heartbeat.
- **Gates**: `check_feature()` (tier_limits) en `is_tool_enabled()`
  (feature_flags) checken eerst de trial-gate; `ai_service._generate`
  retourneert `None` in trial; `create_tenant` (settings.py) blokkeert bij
  `Tenant.query.count() >= trial_tenant_limit()` met 403 NL-bericht.
- **UI**: trial-banner in `templates/cms/base.html` (context processor
  `inject_license_state` in `cms/__init__.py`, alleen bij niet-volledig
  gelicenseerd) + licentiestatus-kaart in Settings → General
  (install_id, status, plan, expires, days left, message, trial_tenant_limit,
  publieke sleutel). Nieuwe Settings: `license_public_key` +
  `trial_tenant_limit`.
- **`app.py`**: CLI `flask license:status`.

### Bugfixes
- `cms/services/license.py::_parse_ts` gaf naive datetime terug →
  `TypeError` bij de dagenberekening; nu timezone-aware (naive → UTC).
- Testhelper-fix: 2FA-POST mag niet binnen een `with c.session_transaction()`
  blok staan (cookie werd overschreven → login verloren).

### Post-deploy fixes (2026-08-02, live op prod)
- Deploy-`rsync --delete` miste de `keys/`-exclude en wist daardoor
  `keys/private.pem` op prod → nieuwe keypair gegenereerd
  (publieke sleutel `4xvSvYw1F9tjTfss0e_6XpdUnPxiOaFdK0shP3cxz-U`), code-default
  bijgewerkt, bestaande installs opnieuw een full-licentie geven + Setting
  `license_public_key` bijwerken. `--exclude='keys/'` staat nu in de README.
- Embedded publieke sleutel vervangen door de echte prod-keypair
  `0O8JlHxzLlOaAEnD26eG4gfPJWALL3mRPbfVpJx93zE` (`cms/services/license.py`
  `DEFAULT_PUBLIC_KEY` + `_general_defaults` in `cms/models/__init__.py`); de
  oude placeholder had geen privésleutel en liet signature-verificatie falen.
  Op prod de bestaande `license_public_key`-Setting-row overschrijven met deze
  waarde (row uit `init_default_settings` overschrijft de code-default).
- Settings-kaart toont bij een geldige full/professional/enterprise-licentie
  "Tenant limit: Unlimited" i.p.v. de verwarrende trial-limit; de trial-limit
  rij verschijnt alleen nog in trial-modus. (Enforcement was al correct:
  `create_tenant` gated alleen in `trial_mode()`.)

### Post-deploy feature: webbeheer in license-dashboard
- Licenties zijn nu vanuit `https://license.iveras.com` te beheren (achter de
  basic-auth login): **Issue license**-formulier (install, plan, days of
  vervaldatum) en een **Revoke**-knop per rij. Nieuwe routes
  `POST /license/issue` en `POST /license/revoke`.
- Refactor: gedeelde helpers `_issue_license`/`_revoke_license` in
  `license-server/app.py`; de CLI (`cli.py license:new`/`license:revoke`) en de
  webroutes gebruiken dezelfde logica. Validatie op plan/days/expires in de
  webroute (plan ∈ full|trial, 1 ≤ days ≤ 3650).
- Tests: `license-server/tests/test_server.py` uitgebreid met
  `TestWebActions` (11 tests: auth 401, issue full/trial/expires, slechte
  plan/days 400, onbekende install 404, revoke + geen actieve license,
  dashboard-pagina bevat actions). Suite: **504 passed**.

### Validatie
- **Tests**: `tests/test_license_ui.py` (14 nieuw: state-machine, banner,
  settings-kaart, tenant-limit 403/201, gates) + `license-server/tests` (20).
  Volledige suite **492 passed, 0 failed**; ruff clean.
- `LICENSE_ENFORCEMENT=off` in `tests/conftest.py` zodat bestaande
  phone/vessel-integratietests niet door trial-gates breken (gate-logica wordt
  apart getest met `monkeypatch.setenv`).

### Nog te doen
- Deploy op prod: pull als `osint`, veilige rsync, `pip install` (cryptography),
  `keys:generate` als `license`-user, bestaande installs een full-licentie geven
  of trial laten verlopen.
- Kernel-reboot op `cloud` (nog openstaand).
- **Wens (afgesproken, niet gestart):** menu-gestuurde restore-wizard
  (`scripts/restore.sh`-achtig maar interactief) die de operator door het
  herstellen "praat": kiezen welk archief, `--key` automatisch zoeken of
  opgeven, backups offloaden, en per stap uitleggen wat er gebeurt.
- **Back-up-huiswerk:** `backup-key.gpg` off-server kopiëren (password manager
  / tweede machine) — pas doen, nog niet uitgevoerd.

---

## August 2 — Telemetrie & License Server fase 1

### Doel
Fase 1 van het telemetrie + licensing-systeem: een centrale `license-server/` op eigen VPS (`license.iveras.com`) die alle OSINT Dashboard installaties registreert en dagelijks systeeminfo ontvangt. Fundament voor Ed25519-licenties (fase 2) en Stripe (fase 3).

### Wijzigingen
- **`license-server/`** (nieuw, eigen deploy): Flask-app met SQLite (stdlib) + gunicorn.
  - `POST /api/register` — registreert een install (idempotent; her-registratie met zelfde token werkt, verkeerd token → 403).
  - `POST /api/telemetry` — dagelijkse heartbeat; partial payloads overschrijven bestaande velden niet.
  - `GET /` + `GET /api/installs` — registry-dashboard met status (online/stale), systeeminfo per install; HTTP Basic Auth (`ADMIN_PASSWORD` verplicht in prod).
  - Tokens worden alleen als SHA-256-hash opgeslagen.
  - `requirements.txt`, `README.md`, `deploy/license-server.service` + `deploy/nginx.conf`.
- **`cms/services/telemetry.py`** (nieuw): client die systeeminfo verzamelt (hostname, lokale IPs, OS/kernel, CPU/RAM/disk via psutil, app-versie) en register + heartbeat stuurt. Silent fail; via `requests` direct (géén Tor/proxy/jitter). Identiteit: env `INSTALL_ID`/`INSTALL_TOKEN` → fallback naar `Setting` (`install_id`/`install_token`, token encrypted).
- **`cms/__init__.py`**: `init_telemetry(app)` aan einde van `create_cms_module` — genereert identiteit + start check-in thread (alleen productie, `FLASK_ENV=production` en niet-testing; skipt in tests/dev).
- **`cms/models/__init__.py`**: `_general_defaults` → `telemetry_enabled` (select, default true) + `telemetry_server_url` (default `https://license.iveras.com`).
- **`app.py`**: CLI `flask telemetry:report` om handmatig een check-in te forceren.
- **`install.sh`**: `.env` krijgt `INSTALL_ID` (`/proc/sys/kernel/random/uuid`) + `INSTALL_TOKEN`; direct na aanmaken best-effort register-call naar de license server.
- **`docker-up.sh` + `docker-compose.yml`**: stabiele identiteit in `.env` + doorgeven aan `app`/`worker` services.
- **`.env.example` / `INSTALL.md`**: telemetry-documentatie.

### Validatie
- License-server smoke test: register/re-register/wrong-token/telemetry/unknown/401-auth/dashboard/health allemaal correct; partial-telemetry-preserves-fields bug gevonden en gefixt.
- Tests: `tests/test_telemetry.py` (14 nieuw) + volledige suite 458 passed, 0 failed.
- Ruff clean op alle gewijzigde bestanden.

### Nog te doen
- **Deploy `license-server/` op `license.iveras.com`** (zie `license-server/README.md`; eigenaar runt dit zelf op de VPS).
- Op prod `git pull` + `sudo systemctl restart osint-dashboard` — daarna `flask telemetry:report` om de eerste registratie te verifiëren.
- Fase 2 (Ed25519-licenties) is inmiddels geïmplementeerd — zie de entry bovenaan.
- Kernel-reboot op `cloud` (nog openstaand).

---

## August 2 — Install audit afgerond (doctor.py op prod, alembic drift, fresh-VM test)

### Doel
De install-audit voor een schone Ubuntu-server deploy volledig afronden: doctor.py op prod, alembic-drift migratie, en een verse-install test.

### Wijzigingen
- **`migrations/versions/d9e8f7a6b5c4_align_schema_with_models.py`**: handgeschreven drift-migratie voor de drie echte model-vs-DB-gaten — `ix_cases_case_number`, `ix_tenants_join_code` (vervangt legacy `uq_tenants_join_code` unique constraint), `social_accounts.finding_id` FK naar `findings.id`. Dialect-veilig (SQLite batch + PostgreSQL); round-trip `downgrade -1`/`upgrade head` geverifieerd op PG.
- **`install.sh`**:
  - Python 3.12-bootstrap zelfvoorzienend: installeert `software-properties-common` als `add-apt-repository` ontbreekt (schone Ubuntu 22.04) en handelt ontbrekend/te oud systeem-python af (was harde exit).
  - `CMS_ENCRYPTION_KEY` wordt gegenereerd met de venv-python i.p.v. systeem-python (kapotte/missende `cryptography` op schone servers).
  - `ProtectHome=true` verwijderd uit `spiderfoot.service` — blokkeerde het schrijven van `/home/osint/.spiderfoot` (EACCES crash-loop).
- **`scripts/doctor.py`**:
  - Alembic-check: vergelijkt nu `current` vs `heads` i.p.v. `alembic check` (dat rapporteert model-drift en claimde vals "FIXED").
  - `check_redis`: skipt netjes als `REDIS_URL` niet in `.env` staat (Redis is optioneel) en crasht niet meer op ontbrekende `redis-cli` (`FileNotFoundError`).
  - `check_env_flask_env` / `check_env_db_ssl_mode`: voegen ontbrekende/foute keys nu zelf toe aan `.env` (`FLASK_ENV=production`, `DB_SSL_MODE=prefer`).
  - OPSEC-check: inline `python -c` snippet had `print(...); for ...` (compound statement na `;`) → altijd `SyntaxError` en lege stdout → vals FAIL. Gefixt naar newline-gescheiden statements.
  - `_set_env`/`_env_value` helpers toegevoegd.

### Validatie
- **Fresh-VM test**: schone Ubuntu 22.04 container met systemd, volledige `install.sh` run — installatie compleet (EXIT=0), SpiderFoot actief, health HTTP 200, doctor 20/21 (alleen verwachte Tor-check). Dit vond de 3 install-bugs hierboven.
- **Doctor op prod**: 21/21 passed na alle fixes.
- **Migrations**: `alembic upgrade head` op verse SQLite én op verse PostgreSQL; nieuwe migratie zowel downgrade als upgrade clean.
- **Tests**: 439 passed, 0 failed; ruff clean op alle gewijzigde bestanden.

### Commits
- `ce82af4` — install + doctor + migration fixes (fresh-VM test)
- `9f99d44` — doctor.py Redis-skip + FLASK_ENV/DB_SSL_MODE auto-fix
- `373c6e4` — doctor.py OPSEC SyntaxError fix

### Nog te doen
- Kernel-reboot op `cloud` (draait 7.0.0-22, geïnstalleerd 7.0.0-28).

---

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

---

## June 30 — Announcement System

### Doel
System-wide announcement feature so super admin can broadcast mandatory popup messages to all users.

### Wijzigingen
- **`cms/models/announcement.py`** (NIEUW): `Announcement` + `AnnouncementAck` models — system-wide, no tenant_id, severity levels, acknowledgment tracking.
- **`cms/models/__init__.py`**: Added `Announcement` + `AnnouncementAck` to imports and `__all__`.
- **`migrations/versions/bd1055cd35b5_add_announcements_tables.py`** (NIEUW): Creates `announcements` + `announcement_acks` tables.
- **`cms/routes/system.py`**: Added `list_announcements`, `create_announcement`, `edit_announcement`, `toggle_announcement`, `delete_announcement`, `ack_announcement` routes. Fixed missing `current_user` import.
- **`templates/cms/announcements/list.html`** (NIEUW): Admin list with severity color-coded cards, toggle/delete.
- **`templates/cms/announcements/form.html`** (NIEUW): Create/edit form.
- **`templates/cms/base.html`**: Added announcement modal JS (shows unacknowledged announcements on page load, POST `/cms/api/announcements/<id>/ack`, cycles through queue). Added nav link under Super dropdown. Added context processor in `app.py` that injects `unacknowledged_announcements` (active, non-expired, unacknowledged by current user).

### Key Decisions
- No tenant_id — announcements are system-wide, visible to all users
- `expires_at` nullable (null = never expires)
- Modal shown on every page load until all active announcements acknowledged
- Acknowledgment is idempotent (unique constraint on announcement_id + user_id)

---

## July 17 — Vervang 2Chat door eigen Baileys WhatsApp microservice

### Doel
2Chat ($31/mnd) vervangen door een zelf-gehoste opensource WhatsApp service (Baileys) voor telefoonnummercheck en WhatsApp Business profiel lookup.

### Wijzigingen
- **`wa-service/`** (NIEUW): Node.js microservice met Baileys v6.7.23
  - `POST /api/check` — checkt of nummer op WhatsApp bestaat + business profiel (description, website, email, category, address, business hours, profile pic)
  - `GET /api/status` — sessie status (connected/disconnected/awaiting_qr/pairing)
  - `GET /api/qr` — QR code voor initiële koppeling
  - `POST /api/pairing` — pairing code alternatief voor QR
  - `POST /api/restart` — reset sessie
  - Auto-reconnect bij disconnect, auth persistente opslag in `auth/`
  - Ondersteunt `PAIRING_PHONE` env var voor automatische pairing
- **`wa-service/Dockerfile`**: Node.js 20-alpine, <120MB, healthcheck
- **`docker-compose.yml`**: `wa-service` container toegevoegd + `wa_auth` volume + `WA_SERVICE_URL` env
- **`cms/services/phone_service.py`**: Nieuwe `_whatsapp_check_baileys()` functie — roept Node.js service aan, mapt response naar bestaand format
- **`cms/workflow/research.py`**: `_phone_check()` gebruikt Baileys als primary, 2Chat als fallback
- **`.env.example`**: `WA_SERVICE_URL` documentatie toegevoegd

### Kostenbesparing
- 2Chat: $31/mnd → €0/mnd (Baileys is MIT, gratis)
- Alleen server resources (marginaal, ~50MB RAM per sessie)

### Status
- Code gecommit en gepusht (`a3de4e5`)
- Initieel authenticeren nog niet gelukt (WhatsApp rate limiting — "later opnieuw proberen")
- Zodra eenmalig gekoppeld, is de sessie persistent bij herstarts

### Andere fixes deze sessie
- Password toggle signup, check_confirm validator fix, auto-refresh findings, soft-delete filter, subdomain JSON error, sortering findings, checkbox overlap, telefooncheck carrier/lijntype/tijdzone

---

## July 18 — Update UI Overhaul: Async + Afbreken + Rollback

### Doel
504 timeout fixen door sync update → background thread + polling. Modal duidelijker maken met Afbreken/Rollback.

### Wijzigingen
- **`cms/routes/system.py`**: `do_update` herschreven naar background thread met file-based task tracking (`/tmp/iveras_update_tasks/`). Nieuwe endpoints: `GET /admin/update-status/<task_id>` (polling), `POST /admin/abort-update/<task_id>`. Rollback endpoint toegevoegd.
- **`static/js/base.js`**: `runUpdate` gebruikt nu polling (elke 2s) i.p.v. enkele fetch. `_updateUI()` met 7 modes (idle/running/done/restarting/error/rolling/aborted). `cancelUpdate()` POST naar abort endpoint.
- **`templates/cms/base.html`**: Modal met dynamische knoppen (Afbreken/Rollback/Opnieuw), JS config URLs voor polling + abort.
- **`static/dist/base.min.js`**: Gebundeld
- **VERSION**: `3.7.1` (gewijzigd in vorige sessie)

### Hoe het werkt
1. Klik ⬆️ Update Now → POST naar do-update → krijgt `task_id` terug (202)
2. Frontend pollt elke 2s `/admin/update-status/<task_id>`
3. Stappen worden live getoond in de modal
4. Na laatste stap: status `"restarting"` → "Server wordt herstart..." → reload
5. Bij fout: "↩ Rollback" verschijnt → herstelt backup + git reset
6. "✕ Afbreken" zet `aborted` flag → thread stopt na huidige stap

### Status
- Gepusht naar `master` (`5111d32`)
- Productie geüpdatet via `git pull && systemctl restart`
- Geen 504 meer mogelijk (nginx ziet alleen korte requests)

### Fixes na async rewrite
- **Rollback 405**: route accepteert nu `POST`
- **PermissionError on chmod**: `backup.sh` owned by root → try/except
- **PATH in subprocess**: `dirname`/`date` not found → `export PATH` in scripts + `_full_env()` helper
- **Abort status stuck**: main-flow checks schrijven nu `status="aborted"` + `_save()`
- **Exception zichtbaar**: traceback wordt getoond in error output i.p.v. "Update crashed"
- **Version bump**: `3.7.1` in `version.py` + changelog entry

### Laatste commit
- `5186c4c` — alles lokaal, GitHub en productie in sync

---

## Vehicle check: findings niet meer overschreven bij heruitvoering

### Probleem
Bij het heruitvoeren van een Vehicle check (of andere actie van hetzelfde type) verdwenen de bevindingen van eerdere uitvoeringen. De `run_action`-functie archiveerde automatisch alle findings van eerdere completed actions van hetzelfde type, en het `case_status`-endpoint filterde archived findings eruit.

### Wijziging
- **`cms/workflow/research.py:130-148`**: Archivering van eerdere findings bij heruitvoering verwijderd. Elke actie behoudt zijn eigen bevindingen; ze worden in de UI gegroepeerd per actie, dus duplicaten worden zo voorkomen.

### Tests
422 passed — geen regressie.
