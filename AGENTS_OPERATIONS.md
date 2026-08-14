# Operations — Database, Setup, Deploy, Encryption, Git

## Entrypoint & Run
- `app.py` is the single Flask entrypoint. Dev: `/usr/local/bin/python3 app.py` (port from `$PORT`, default 5000). **Python 3.12 required**.
- **macOS AirPlay conflict**: Port 5000 may be reserved. Set `PORT=5001` or disable AirPlay Receiver in System Settings.
- CMS module initialized via `cms/__init__.py::create_cms_module(app)`.
- **Production push**: Before deploying, change `debug=True` → `debug=False` in `app.py`.
- Dev with SpiderFoot: `./start.sh start`. Stop: `./start.sh stop`.

## Telemetry & Licensing
- Central license server lives in `license-server/` (own VPS, `license.iveras.com`) — see `license-server/README.md` for deploy.
- Client: `cms/services/telemetry.py`. Registers at install, then a daily heartbeat on a background thread (**production only** — gated on `FLASK_ENV=production`, `app.testing`, and `TELEMETRY_DISABLED`).
- Identity: env `INSTALL_ID`/`INSTALL_TOKEN` (written by `install.sh`/`docker-up.sh`) → fallback to `Setting` (`install_id`, encrypted `install_token`), auto-generated on first prod start.
- Settings: `telemetry_enabled` (toggle) + `telemetry_server_url` in Settings → General. CLI force: `flask telemetry:report`.
- **Licensing (fase 2, Ed25519)**: de server geeft elke install automatisch een trial-licentie (`TRIAL_DAYS`, default 30); `license:new`/`license:revoke`/`license:list` via `license-server/cli.py`. De app verifieert offline met de ingebakken publieke sleutel (`cms/services/license.py`), revocatie/verloop komt online via de check-in.
- **App-side gates (soft trial)**: `trial_blocked(feature)` blokkeert `ai`, `spiderfoot`, `vessel`, `phone`; tenants gelimiteerd op `trial_tenant_limit` (default 1) via `create_tenant`. Central: `check_feature()` (tier_limits) + `is_tool_enabled()` (feature_flags).
- **Uitschakelen**: `LICENSE_ENFORCEMENT=off` in de app `.env`, óf een geldige full-licentie op de install.
- Settings: `license_public_key` (overschrijfbaar, default = ingebakken publieke sleutel) + `trial_tenant_limit` in Settings → General. UI: banner in header (trial/verlopen/revoked) + licentiestatus-kaart in Settings → General. CLI: `flask license:status`.
- Tests: `license-server/tests/test_server.py` (20) + `tests/test_license_ui.py` (14). `LICENSE_ENFORCEMENT=off` staat in `tests/conftest.py` zodat bestaande integratietests niet door trial-gates breken.

---

## Database
### Dev database strategy (hybrid)
- **Default**: SQLite (`cms.db`) — zero-config, perfect voor lokale dev.
- **Optioneel**: PostgreSQL (matcht productie) — zet `DATABASE_URL` in `.env`.
  ```bash
  # PostgreSQL opzetten op macOS:
  brew install postgresql@16 && brew services start postgresql@16 && createdb cms_dev
  # In .env:
  # DATABASE_URL=postgresql://$(whoami)@localhost:5432/cms_dev
  ```
- **Graceful fallback**: Als Postgres onbereikbaar is (bv. vergeten te starten), valt `app.py` terug op SQLite met een `WARNING` in de log. Geen crash.
- **Tests**: Altijd SQLite (`conftest.py`). Snel, geen externe services. SQLite-only gedrag wordt gemarkeerd met `@pytest.mark.skip_on_postgres`.
- **CI** (`.github/workflows/ci.yml`):
  - `test` job: SQLite (bestaand, snel)
  - `integration-postgres` job: verplichte PostgreSQL 16-check met RLS-, tenant- en worker-contexttests

### Schema management
- Schema via **Alembic** (`migrations/`):
  - **New DB** (no tables): `alembic upgrade head`
  - **Existing DB** (tables, no `alembic_version`): `alembic stamp head`
  - **Already migrated**: idempotent
- Admin (`admin@localhost`/`changeme123`) created by `cms/__init__.py` data migration.
- **Alembic CLI**: `DATABASE_URL="sqlite:///test.db" python3 -m alembic upgrade head` (no Flask CLI needed).
- **New migration**: `DATABASE_URL="..." python3 -m alembic revision --autogenerate -m "description"`.

### Default Settings Initialization (`cms/models/__init__.py:init_default_settings`)
- Called at every Flask startup (from `cms/__init__.py:147`), and after every settings save/reset.
- Creates missing default settings from a hardcoded list (~40 settings: API keys, feature flags, appearance, etc.).
- **Since June 18**: Also patches existing settings that are missing `options` (for select-type settings), `value_type`, or `display_order` fields by copying from the defaults list. This fixes settings that were created by older code versions before these columns were added.

### PostgreSQL vs SQLite notes
- PostgreSQL enforces `VARCHAR(n)`; SQLite ignores it. ALL encrypted columns MUST be `String(500)` minimum.
- Never mutate `created_at` on ORM objects directly (crashes SQLite).
- **Manual migrations**: Write `upgrade()`/`downgrade()` with `_has_column()` guards for idempotence on both SQLite and PostgreSQL.

---

## Encryption Key Persistence
- **`CMS_ENCRYPTION_KEY` env var** — takes precedence.
- **`.cms_key` file** — fallback; created with `chmod 600` on first auto-generate.
- `cms/config.py` defines `Config`, `DevelopmentConfig`, `ProductionConfig`, `TestingConfig`.
- Picked via `get_config()` from `app.py:143-145` based on `FLASK_ENV` (default: `development`).
- `DevelopmentConfig`: `WTF_CSRF_ENABLED = True` (inherited), `SESSION_COOKIE_SECURE = False`.
- `ProductionConfig`: `WTF_CSRF_ENABLED = True`, `SESSION_COOKIE_SAMESITE = 'Strict'`.
- `TestingConfig`: in-memory SQLite, CSRF off.
- **Session**: `PERMANENT_SESSION_LIFETIME = 8h`, `SESSION_COOKIE_HTTPONLY = True`.
- **Uploads**: `MAX_CONTENT_LENGTH = 16MB`.
- **Security headers** (`@app.after_request`): `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Strict-Transport-Security`.
- **SQLite**: `SQLALCHEMY_ENGINE_OPTIONS` (pool_size, pool_recycle, pool_pre_ping) auto-removed for SQLite.
- **CSRF**: Active via `flask_wtf.CSRFProtect`. All forms have `{{ csrf_token() }}`. JSON API POST routes are `@csrf.exempt`.

---

## Thread Safety
- **`active_searches`**: Protected by `_searches_lock` (`threading.Lock()`).
- **`_LAST_MARINEPLAN_CALL`**: Protected by `_marineplan_lock`.
- **Shell injection**: All `subprocess.run()` uses list arguments, never `shell=True`.
- **`do_update()` crash safety**: Wrapped in try/except to return JSON on crash.

---

## Git
- Rollback: `git reset --hard <hash>`.
- Push: `git push` (remote: `origin/master`).

---

## Always Use Full Paths
- Production commands MUST use `/opt/osint-dashboard`:
  ```bash
  cd /opt/osint-dashboard && git pull origin master && sudo systemctl restart osint-dashboard
  ```

---

## Server Diagnostics (`scripts/doctor.py`)
- `sudo python3 scripts/doctor.py` — checks 11 items (osint user, home dir, .spiderfoot, .git perms, flask_session, pip deps, .env key, alembic, SF service, Flask health, SF URL).
- `sudo python3 scripts/doctor.py --dry-run` to preview.
- Uses venv Python (`/opt/osint-dashboard/venv/bin/python3`).

---

## Production Install (`install.sh`)
- **Gunicorn logging**: `--access-logfile /var/log/osint-dashboard/access.log --error-logfile /var/log/osint-dashboard/error.log`.
- **Nginx tuning**: `proxy_buffer_size 8k`, `proxy_buffers 8 8k`, `proxy_read_timeout 120s`.
- **SpiderFoot service**: `ProtectHome=off` (blocks access to `/home/osint/.spiderfoot`).
- **Backup cron**: 4x daily (00:00, 06:00, 12:00, 18:00) via `/etc/cron.d/osint-dashboard-backup`.
- **Sudoers**: `git`, `chown`, `systemctl` allowed for passwordless update from GUI.

---

## Password Reset Flow
- No passwords in email — `send_password_reset_email()` sends a reset link.
- `User` model: `password_reset_token` (VARCHAR(128), SHA-256) + `password_reset_expires` (TIMESTAMP, 48h TTL).
- `GET/POST /auth/set-password/<token>` — public, 8+ char password + confirm. Token is one-time use.
- `create_user()` with `send_email=True` generates a reset token instead of including raw password.

---

## Update Backup
- **GUI update** (`POST /cms/admin/do-update`): draait `scripts/backup.sh` voor de update start (stap 1). Als backup.sh niet bestaat, valt het terug op SQLite-only `cp`.
- **CLI update** (`scripts/update.sh`): draait backup als stap 1/5.

## Restore from Backup (`scripts/restore.sh`)
- `./scripts/restore.sh --list` — toont beschikbare backups met datum/tijd.
- `./scripts/restore.sh` — herstelt van de laatste backup (vraagt bevestiging).
- `./scripts/restore.sh --backup /pad/naar/archive.tar.gz.gpg` — specifieke backup.
- `./scripts/restore.sh --dry-run --backup ...` — laat zien wat er zou gebeuren, zonder wijzigingen.
- **Wat het herstelt**: database (PostgreSQL/SQLite), uploads, sessies, `.env`, nginx config, systemd services, SpiderFoot passwd, migraties.
- **Veiligheid**: maakt altijd een `pre_restore_` backup van de huidige staat vóór overschrijven.
- **Belangrijk**: de backup key (`backups/backup-key.gpg`) is nodig om te decrypteren. Zonder key is restore onmogelijk.

## Backup Verification (`scripts/verify_backup.sh`)
- `./scripts/verify_backup.sh` — checks file integrity, gzip validity, SQL syntax, PostgreSQL restore dry-run (if available), SQLite `PRAGMA integrity_check`.
- Exit codes: 0 (OK), 1 (no backup), 2 (verification failed), 3 (cleanup error).
- `./scripts/verify_backup.sh --cleanup` removes `/tmp/iveras_backup_verify_*` dirs older than 7 days.
