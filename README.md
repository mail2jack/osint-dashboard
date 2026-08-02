# Iveras OSINT Dashboard

Open-source intelligence case management system with automated investigations, SpiderFoot integration, and multi-tenant SaaS support.

## Quick Start (Ubuntu Server)

```bash
sudo apt install -y wget
wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/master/install.sh
chmod +x install.sh
sudo ./install.sh
```

See [INSTALL.md](INSTALL.md) for detailed instructions and [MANUAL.md](MANUAL.md) for the full user manual.

## Features

- **Case Management** — Investigations with clients, subjects, and findings
- **OSINT Search** — Brave API, DuckDuckGo fallback, SpiderFoot automation
- **Multi-tenant SaaS** — Isolated tenants with role-based access (owner/admin/investigator)
- **GDPR Compliance** — DSAR/erasure workflows, breach notifications (Art. 33-34), DPA register (Art. 28), audit logging, field-level encryption
- **Face Recognition** — Client-side encoding via face-api.js
- **Phone Enrichment** — Carrier, region, WhatsApp/Telegram presence
- **Vehicle Data** — Dutch RDW registry lookup
- **Kadaster BAG** — Dutch address verification
- **Interpol Checks** — Red Notice (wanted) and Yellow Notice (missing) lookups
- **2FA** — TOTP-based two-factor authentication
- **Screenshot Capture** — Playwright-based evidence screenshots
- **PDF Export** — Case reports via WeasyPrint
- **Stripe Billing** — SaaS subscription management

## Documentation

| Document | Description |
|---|---|
| [INSTALL.md](INSTALL.md) | Fresh Ubuntu server installation guide |
| [MANUAL.md](MANUAL.md) | Full user manual (features, settings, troubleshooting) |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Tech Stack

- **Backend:** Python 3.12+, Flask, SQLAlchemy, PostgreSQL
- **Frontend:** Vanilla JS, esbuild, Bootstrap 5
- **OSINT:** SpiderFoot, Playwright, Brave Search API
- **Infrastructure:** Nginx, Gunicorn, Systemd, Certbot
- **SaaS:** Stripe, multi-tenant with tenant isolation

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
