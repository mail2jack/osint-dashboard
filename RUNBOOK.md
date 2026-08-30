# Iveras OSINT Dashboard — Production Runbook

Operationele handleiding voor de productie-installatie op `/opt/osint-dashboard`
(systemd: `osint-dashboard`, user: `osint`, localhost:5000). De license-server
heeft een eigen runbook in `license-server/README.md`.

**Uitgangspunten:**
- `master` is production candidate: er komt niets op prod zonder preflight,
  tests en een groene CI.
- Geen automatische deploys, geen automatische rollbacks, geen destructieve
  git-commando's zonder expliciete operatorbevestiging.
- Elke deploy is vergrendeld (`flock`), gelogd met timestamp + commit SHA en
  nooit half-afgemaakt (bij fout stopt het release).
- Scripts loggen nooit secrets en printen nooit .env-waarden.

---

## 1. Deploy

```bash
# Plan-only (draait preflight, wijzigt niets):
sudo /opt/osint-dashboard/scripts/deploy.sh --dry-run

# Master deployen (preflight → backup → pull → deps → build → migrate → restart → health):
sudo /opt/osint-dashboard/scripts/deploy.sh

# Specifieke commit of tag deployen (rollback-doel / gerichte release):
sudo /opt/osint-dashboard/scripts/deploy.sh <commit-sha|tag>
```

Wat er gebeurt:
1. **`scripts/preflight.sh`** — read-only gate: `.env`-sleutels aanwezig
   (FLASK_ENV=production, DATABASE_URL, CMS_ENCRYPTION_KEY, SECRET_KEY),
   `doctor.py --dry-run` (21 checks, zonder wijzigingen), PostgreSQL
   bereikbaar, `pip-audit` zonder kwetsbaarheden. Geen preflight = geen deploy.
2. **`scripts/update.sh`** — exclusief via `flock` (`.deploy.lock`):
   versleutelde backup (stopt als die mislukt) → git pull (of de gepinde
   commit) → pip install → frontend-build → `alembic upgrade head` → restart →
   `/api/v1/health`-check. Bij elke stap fout = stop + e-mail aan superadmins.
3. Commit-SHA wordt vastgelegd in `/opt/osint-dashboard/.deployed_sha` en in
   `/opt/osint-dashboard/logs/update-<timestamp>.log`.

`scripts/update.sh` kan los draaien (zonder preflight) voor een herstel-update:
`sudo /opt/osint-dashboard/scripts/update.sh`.

---

## 2. Health & monitoring

| Endpoint | Betekenis |
|---|---|
| `/health?quick=1` | Liveness (licht): DB-ping, spiderfoot-cache. Gebruikt door doctor.py. |
| `/health` | **Readiness**: DB, Redis (alleen indien `REDIS_URL` gezet), migraties (alembic current==heads), disk/memory, externe services. `503` bij degradatie. |
| `/api/v1/health` | Pure liveness (`{"status":"ok"}`) — gebruikt door de deploy health-check. |
| `/metrics` | Prometheus-metrieken. |

Op één VPS met Grafana: scrape `/health` en alarmeer op non-200 of
`status != ok`. Ready betekent dat de app requests kan verwerken; niet-ready
wordt geëxposeerd via HTTP 503 (nginx kan dan eigen 503-server pagina's tonen).

---

## 3. Rollback

**Er is nooit een automatische rollback.** Bij een problematische release:

1. Zet de app vast (service blijft draaien, oude code is gewoon weg) en kijk
   naar de deploylog + app-log:
   ```bash
   journalctl -u osint-dashboard -n 200 --no-pager
   sudo cat /opt/osint-dashboard/logs/update-*.log | tail -100
   ```
2. Deploy de vorige commit opnieuw (de backup van deze deploy ligt al klaar):
   ```bash
   sudo /opt/osint-dashboard/scripts/deploy.sh <vorige-commit-SHA>
   ```
   (haal de SHA uit `.deployed_sha` vóór de deploy of uit `git reflog`.)
3. Schema-afwijkingen: `alembic downgrade` **alleen op expliciete, handmatige
   instructie** en nooit automatisch. Een downgrade is destructief; weeg
   backup-restore (4) af tegen downgrade. Zet de app in onderhoud vóór je dit
   doet.

---

## 4. Incident

1. **Vaststellen**: `curl -s localhost:5000/health | head -c 500` → is de app
   ready? `systemctl status osint-dashboard`, `journalctl -u osint-dashboard -n 200`.
2. **Diagnose**: `sudo python3 /opt/osint-dashboard/scripts/doctor.py --dry-run`
   (wijzigt niets); zonder `--dry-run` fix doctor zelf wat fixbaar is
   (chown, systemd-start, `.env`-DB_SSL_MODE, migraties).
3. **DB/Redis/migraties**: readiness-`/health` toont per-component de status.
   `SELECT`-probe: `pg_isready -h <host>`; Redis alleen relevant als
   `REDIS_URL` in `.env` staat.
4. **Backup-verificatie tijdens incident**: `./scripts/verify_backup.sh` —
   herstelt de laatste backup naar `/tmp` en valideert.
5. **Escalatie/Sentry**: stacktraces in `/var/log/osint-dashboard/error.log`;
   Sentry-DSN uit `.env` of Settings → General (`sentry_dsn`).
6. Leg het incident vast in `AGENTS_CHANGELOG.md` na afloop.

---

## 5. Backup & restore

```bash
# Backups: 4x per dag via /etc/cron.d/osint-dashboard-backup → /opt/osint-dashboard/backups
sudo -u osint bash /opt/osint-dashboard/scripts/backup.sh /opt/osint-dashboard/backups

# Verificatie (isolated PostgreSQL restore, counts, encryption, uploads, license DB/key):
# Bronst van de DR-verbindingsomgeving uit /etc/default/osint-dr (dr_setup.sh);
# een kale aanroep is daardoor voldoende, zonder handmatig sourcen.
/opt/osint-dashboard/scripts/verify_backup.sh /opt/osint-dashboard/backups

# Rapporten staan in reports/dr/; dit wijzigt nooit productie.
# RPO/RTO-doelen en systemd timer: DISASTER_RECOVERY.md
/opt/osint-dashboard/scripts/verify_backup.sh --cleanup

# Restore (maakt eerst een pre_restore_ backup van de huidige staat):
/opt/osint-dashboard/scripts/restore.sh --list
/opt/osint-dashboard/scripts/restore.sh --dry-run --backup /opt/osint-dashboard/backups/<bestand>
/opt/osint-dashboard/scripts/restore.sh --backup /opt/osint-dashboard/backups/<bestand>
```

**Kritiek**: `backups/backup-key.gpg` is de decryptiesleutel. Zonder die key
is restore onmogelijk. Bewaar een kopie op een andere plek dan de server.
Neem ook `/opt/license-server/keys/private.pem` en `data/license.db` op in een
secundaire back-up (zie `license-server/README.md`).

---

## 6. Secret rotation

| Secret | Waar | Rotatie-impact |
|---|---|---|
| `CMS_ENCRYPTION_KEY` | `.env` / `.cms_key` | **Met multi-key fallback**: voeg nieuwe key toe aan `CMS_ENCRYPTION_KEYS` (comma-separated), draai `flask rotate-encryption`, dan `CMS_ENCRYPTION_KEY` updaten. Zie procedure hieronder. |
| `TOTP secret` | Database (`users.totp_secret`) | Via database update: `psql -c "UPDATE users SET totp_secret = '<new>' WHERE email = '<user>'"`. Gebruiker moet 2FA opnieuw instellen. |
| `SECRET_KEY` | `.env` | Logt alle sessies uit (onschuldig, wenselijk bij verdachte activiteit). |
| `FLASK_ENV` / `DB_SSL_MODE` | `.env` | `DB_SSL_MODE` mag alleen sterker (require/verify-ca/verify-full); doctor.py zet `require`. |
| `ADMIN_PASSWORD` | `.env` (license-server) | Via `license-server/README.md`. |
| `LICENSE_ADMIN_SECRET` | `.env` (license-server) | Roteren = herstarten; oude secret vervalt direct. |
| Stripe/API-keys | `.env` + Settings | Stripe: `STRIPE_*`; overige (overheid/brave/rdw) in Settings → General; roteer daar en update `Setting`. |

### CMS_ENCRYPTION_KEY rotatie (multi-key)

Zero-downtime rotatie met behoud van toegang tot met oude sleutel versleutelde data:

```bash
# Stap 1: Nieuwe key genereren
NEW_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Stap 2: Nieuwe key toevoegen aan CMS_ENCRYPTION_KEYS (bestaande key behouden)
# In .env: CMS_ENCRYPTION_KEY=<nieuwe-key>
# In .env: CMS_ENCRYPTION_KEYS=<oude-key>,<eventuele-andere-oude-keys>

# Stap 3: Data re-encrypten met nieuwe sleutel
cd /opt/osint-dashboard && source venv/bin/activate
flask rotate-encryption --verbose

# Stap 4: Verifiëren dat alles werkt
flask verify-encryption --verbose

# Stap 5: Herstarten
./start.sh restart
```

Na verificatie: verwijder oude key uit `CMS_ENCRYPTION_KEYS`.

### TOTP secret rotatie

```bash
# Via psql (vanaf VPS als development of osint user)
psql 'postgresql://osint:<password>@localhost:5432/osint_db' -c \
  "UPDATE users SET totp_secret = '<nieuw-secret>' WHERE email = '<email>' RETURNING email;"
```

Gebruiker moet daarna 2FA opnieuw instellen in authenticator app.

Rotatie-stappen in algemeen: (1) nieuwe waarde genereren, (2) in `.env` zetten
(`chmod 600`), (3) testen via preflight + `/health`, (4) opnieuw deployen of
`systemctl restart osint-dashboard`, (5) oude waarde intrekken.

---

## 7. Verwijzingen

- Architectuur: `AGENTS_ARCHITECTURE.md` · Monitoring: `AGENTS_MONITORING.md` ·
  OPSEC: `AGENTS_OPSEC.md` · License-server deploy: `license-server/README.md`.
- Preflight: `scripts/preflight.sh` · Deploy: `scripts/deploy.sh` +
  `scripts/update.sh` · Backup/restore: `scripts/backup.sh`,
  `scripts/restore.sh`, `scripts/verify_backup.sh` · Diagnose:
  `scripts/doctor.py`.
