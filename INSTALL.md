# Iveras OSINT Dashboard — Fresh Ubuntu Server Installation

This guide covers installing Iveras OSINT Dashboard on a clean Ubuntu 22.04+ server.

## Overview

The automated `install.sh` script handles everything. You only need a cloud VM and root access.

**What gets installed:**
- Python 3.12 (via deadsnakes PPA if needed)
- Node.js 22.x (via NodeSource)
- PostgreSQL 16
- Nginx reverse proxy
- SpiderFoot OSINT automation
- Playwright Chromium (PDF/screenshots)
- Let's Encrypt SSL (optional)
- Systemd services with auto-start
- UFW firewall + Fail2ban

---

## 1. Prerequisites

- **Ubuntu 22.04** or **Debian 12** server
- Root access (`sudo`)
- Ports **80** and **443** reachable from the internet
- (Optional) A domain name pointing to the server's IP

---

## 2. Run the Install Script

Log in to your server via SSH and run:

```bash
sudo apt update
sudo apt install -y wget
wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/master/install.sh
chmod +x install.sh
sudo ./install.sh
```

**What happens during installation:**

1. System dependencies are installed (PostgreSQL, Nginx, Certbot, etc.)
2. Python 3.12 is installed if your system has an older version
3. Node.js 22.x is installed from NodeSource
4. The repository is cloned to `/opt/osint-dashboard`
5. A Python virtual environment is created with all dependencies
6. Frontend assets are built (CSS/JS bundling via esbuild)
7. Playwright Chromium is installed
8. PostgreSQL is configured with a random password
9. SpiderFoot is installed and configured
10. Nginx is set up as a reverse proxy
11. **You'll be prompted for domain names** (press Enter for IP-only)
12. **You'll be prompted for a Let's Encrypt email** (required for SSL)
13. SSL certificates are obtained (if domains provided)
14. UFW firewall and Fail2ban are configured
15. Systemd services are created and enabled
16. **You'll be prompted to install Tor** (optional, for anonymous searches — enable later in Settings → OPSEC)
17. A health check verifies both Iveras and SpiderFoot are running

## 3. After Installation

### First Login

Open your browser and navigate to:

- **With domain:** `https://your-domain.com`
- **With IP:** `http://<server-ip>`

Login with:

- **Email:** `admin@localhost`
- **Password:** `changeme123`

**Change the password immediately** after first login.

### Configure API Keys

Edit `/opt/osint-dashboard/.env` and add your API keys:

| Key | Service | Get it at |
|---|---|---|
| `BRAVE_API_KEY` | Web search | https://brave.com/search/api/ (free, 2k queries/month) |
| `OVERHEID_API_KEY` | Dutch government data | https://overheid.io |
| `HIBP_API_KEY` | Breach checking | https://haveibeenpwned.com/API/Key |
| `TWOCHAT_API_KEY` | WhatsApp integration | https://app.2chat.io |

After editing, restart the service:

```bash
sudo systemctl restart osint-dashboard
```

You can also set API keys via the web UI at **Settings > API Keys**.

### Telemetry & License Server

This project reports basic install info to the Iveras license server
(`https://license.iveras.com`) so the operator can track all deployed
installations and later enforce licenses:

- **At install:** `install.sh` generates `INSTALL_ID` + `INSTALL_TOKEN`, writes
  them to `/opt/osint-dashboard/.env` and registers the install once.
- **Daily:** the app sends a heartbeat with system info (hostname, local IPs,
  public IP, OS/kernel, CPU/RAM/disk, app version). This runs on a background
  thread in production only.
- **On upgrade:** if `INSTALL_ID` is missing, the app auto-generates one on
  first start and stores it in the `Setting` table.

To disable: **Settings → General → Telemetry**, set `telemetry_enabled` to
`Disabled`, or set `TELEMETRY_DISABLED=1` in `.env`. The server URL is
configurable via `telemetry_server_url` (default `https://license.iveras.com`).

Force a check-in from the CLI:

```bash
sudo -u osint /opt/osint-dashboard/venv/bin/flask telemetry:report
```

### Service Management

```bash
sudo ./start-server              # Show status
sudo ./start-server start        # Start all services
sudo ./start-server stop         # Stop all services
sudo ./start-server restart      # Restart all services
sudo ./start-server logs         # Live app logs
sudo ./start-server logs-sf      # Live SpiderFoot logs
sudo ./start-server update       # One-click update (git pull + pip + frontend build + migrations + restart)
```

Or use systemd directly:

```bash
sudo systemctl status osint-dashboard
sudo systemctl status spiderfoot
sudo journalctl -u osint-dashboard -f
```

---

## 4. Directory Layout

```
/opt/osint-dashboard/
├── app.py              # Flask application entry point
├── cms/                # Core CMS module
├── static/             # Static assets (CSS, JS, images)
│   └── dist/           # Built/bundled files (esbuild output)
├── templates/          # Jinja2 templates
├── venv/               # Python virtual environment
├── .env                # Environment configuration
├── install.sh          # Install script
├── update.sh           # Update script
├── start-server        # Service management script
└── scripts/
    ├── backup.sh       # Automated backup script
    ├── doctor.py       # Server diagnostics tool
    └── update.sh       # CI-friendly update script
```

---

## 5. Updating

The dashboard checks for updates automatically and shows a banner when a new version is available. Click the **Update** button or run:

```bash
sudo ./start-server update
```

This runs `git pull`, updates Python packages, rebuilds frontend assets, applies migrations, and restarts services.

---

## 6. Backups

A cron job is installed at `/etc/cron.d/osint-dashboard-backup` that runs backups 4x daily. Backups are stored in `/opt/osint-dashboard/backups/`.

To run a manual backup:

```bash
sudo /opt/osint-dashboard/scripts/backup.sh /opt/osint-dashboard/backups
```

---

## 7. Troubleshooting

### Health check fails

```bash
curl http://localhost:5000/health
```

If it doesn't return `{"status":"ok"}`, check the logs:

```bash
sudo journalctl -u osint-dashboard -n 50 --no-pager
```

### SpiderFoot not reachable

```bash
curl http://127.0.0.1:5001
```

Check SpiderFoot logs:

```bash
sudo journalctl -u spiderfoot -n 50 --no-pager
```

### Diagnose common issues

```bash
sudo python3 /opt/osint-dashboard/scripts/doctor.py
```

This checks: user permissions, service health, SSL renewal, backup cron, Python dependencies, Playwright, Redis, and more.

### Reset admin password

```bash
sudo /opt/osint-dashboard/venv/bin/python3 -c "
from app import app
from cms.models import User, db
app.app_context().push()
u = User.query.filter_by(username='admin').first()
if u:
    u.password = 'changeme123'
    db.session.commit()
    print('Password reset to changeme123')
"
```

---

## 8. SSL Certificate Renewal

Let's Encrypt certificates are automatically renewed via `certbot.timer`. To check renewal status:

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

---

## 9. Security Notes

- Default admin password **must** be changed on first login
- The `osint` system user is created automatically — do not create it manually
- API keys stored in `.env` are readable by `osint` user only
- Field-level encryption uses `CMS_ENCRYPTION_KEY` — keep this safe
- UFW blocks everything except SSH (22), HTTP (80), HTTPS (443)
- Fail2ban bans IPs after 5 failed SSH attempts and 5 failed Nginx auth attempts
- For production, set `FLASK_ENV=production` and `DB_SSL_MODE=require` in `.env`

---

## 10. Docker Installation (Alternative)

A Docker Compose setup is available for local development, testing, or containerized
deployment. It runs the app plus PostgreSQL, Redis, and the optional WhatsApp service.

**What you get:**
- App (gunicorn) + PostgreSQL 16 + Redis 7
- Migrations run automatically on startup
- Optional background worker (RQ) via `--profile worker`
- Optional WhatsApp service (`wa-service`)

### Quick start (one command)

Prerequisite: [Docker](https://docs.docker.com/engine/install/) with the Compose plugin.

```bash
./docker-up.sh
```

The script:
1. Creates a `.env` with random keys (if it doesn't exist yet)
2. Builds and starts the containers
3. Waits until the app responds
4. Prints the login details

First login: `admin@localhost` / `changeme123` (change immediately).

### Manual Docker Compose

```bash
# 1. Create .env in the repo root (all values are required)
cat > .env <<'EOF'
DB_PASSWORD=een-sterk-wachtwoord
CMS_ENCRYPTION_KEY=<32-byte base64 key>
SECRET_KEY=<willekeurige string>
PORT=5000
EOF
```

Generate keys:

```bash
python3 -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python3 -c "import secrets;print(secrets.token_hex(32))"
```

```bash
# 2. Build and start
docker compose up -d --build

# 3. Check
docker compose ps
curl http://localhost:5000/health
```

### Docker commands

| Command | Purpose |
|---|---|
| `docker compose up -d --build` | Build and start / update |
| `docker compose down` | Stop and remove containers |
| `docker compose logs -f app` | Follow app logs |
| `docker compose restart app` | Restart the app |
| `docker compose --profile worker up -d` | Enable the background worker |

**Notes:**
- The Compose setup does **not** include Nginx, SSL, SpiderFoot, or the firewall
  hardening that `install.sh` provides — for production bare-metal use `install.sh`.
- Database is stored in the `pgdata` volume; uploads in the `uploads` volume.
- Keep `.env` safe — it contains the encryption key and secrets.
