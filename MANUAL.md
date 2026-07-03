# Iveras OSINT Case Management System - Manual

**Version:** 3.6.0  
**Last Updated:** July 2026

---

## Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Getting Started](#getting-started)
4. [Dashboard](#dashboard)
5. [Cases](#cases)
6. [Clients](#clients)
7. [Subjects](#subjects)
8. [Search](#search)
9. [OSINT Tools](#osint-tools)
10. [SpiderFoot Integration](#spiderfoot-integration)
11. [Face Recognition](#face-recognition)
12. [Vehicle Data (RDW)](#vehicle-data-rdw)
13. [Reminders](#reminders)
14. [Settings](#settings)
15. [User Management](#user-management)
16. [Two-Factor Authentication (2FA)](#two-factor-authentication-2fa)
17. [Audit Log](#audit-log)
17. [Keyboard Shortcuts](#keyboard-shortcuts)
18. [API Endpoints](#api-endpoints)
19. [Troubleshooting](#troubleshooting)
20. [Changelog](#changelog)

---

## Introduction

Iveras OSINT Case Management System combines open-source intelligence gathering with professional case management capabilities for security researchers, investigators, and legal professionals.

### Key Features

- **Case Management** - Create and manage investigation cases with clients
- **Subject Tracking** - Track persons, companies, vehicles, and other entities
- **OSINT Search** - Web search with Brave API and DuckDuckGo fallback
- **Global Search** - Search across all cases, clients, subjects, findings
- **Face Recognition** - Encode and match faces using face-api.js
- **Vehicle Data** - Dutch RDW vehicle registry lookup
- **Social Media ID** - Extract social media IDs from profile pages
- **Screenshot Capture** - Save and manage evidence screenshots
- **SpiderFoot Integration** - OSINT scanning via local SpiderFoot instance
- **Reminders** - Set follow-up reminders for cases and subjects
- **Audit Logging** - Track all user actions for compliance
- **Role-Based Access** - Admin, Senior Investigator, Junior Investigator roles
- **Two-Factor Authentication** - TOTP-based 2FA met authenticator app, optioneel per gebruiker
- **Kadaster BAG Lookup** — Verify Dutch addresses against the national BAG registry
- **Phone Enrichment** — Validate and enrich phone numbers (carrier, region, WhatsApp/Telegram)
- **Interpol Check** — Check subject names against INTERPOL Red Notices (wanted) and Yellow Notices (missing)
- **Politiebureau Lookup** — Find nearest police station for any address
- **Update Notifications** — In-app banner when a new version is available, with one-click update

---

## Installation

Two methods: **one-command server install** (recommended for production) or **manual setup** (for development/macOS).

> **Important:** The install script creates a dedicated `osint` system user automatically.  
> You do **not** need to create this user yourself — just run `sudo ./install.sh` as your cloud VM user.

---

### Option A: One-command Server Install (Ubuntu/Debian — recommended)

```bash
sudo apt install -y wget
wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/saas-migration/install.sh
chmod +x install.sh
sudo ./install.sh
```

The script installs everything automatically:

- **Python 3.12+** — auto-installs via deadsnakes PPA if system Python is too old
- **Node.js 22.x** — from NodeSource (Ubuntu repos are too old for esbuild)
- **PostgreSQL** (random password generated, auto-configured)
- **Nginx** reverse proxy (Iveras + SpiderFoot routes)
- **SpiderFoot** (git clone + venv + digest auth)
- **Playwright Chromium** — for PDF/screenshot generation
- **SSL via Let's Encrypt** (optional, for one or more domains)
- **Systemd services** (`osint-dashboard`, `spiderfoot`, optionally `osint-bot`)
- **UFW firewall** (SSH/HTTP/HTTPS)
- **Fail2ban** (SSH + Nginx jails)
- **Health check** endpoint verification

#### What you'll be asked

1. **Domain name(s)** — Enter one or more space-separated domains for SSL (e.g. `joost.iveras.nl joost.iveras.com`), or press Enter for IP-only access.
2. **Let's Encrypt email** — Required for certificate expiry notifications.

#### After installation

1. Edit API keys in `/opt/osint-dashboard/.env`:
   - `BRAVE_API_KEY` — for web search (free: https://brave.com/search/api/)
   - `OVERHEID_API_KEY` — for Dutch government data (https://overheid.io)
   - `HIBP_API_KEY` — for breach checking (https://haveibeenpwned.com/API/Key)
   - `TWOCHAT_API_KEY` — for WhatsApp integration (https://app.2chat.io)
2. SpiderFoot password is shown in the install output — save it.
3. Log in at `https://your-domain.com` with `admin` / `changeme123` (change immediately).

**Update notifications** are automatically enabled — a banner appears on the dashboard when a new version is available.

#### Prerequisites

- **Ubuntu 22.04+** or **Debian 12+**
- Root access (sudo)
- Ports 80/443 open (or access to the server's IP)
- Optional: domain name(s) pointing to the server for SSL

#### Service Management

```bash
sudo ./start-server              # Show status (default)
sudo ./start-server start        # Start all services
sudo ./start-server stop         # Stop all services
sudo ./start-server restart      # Restart all services
sudo ./start-server logs         # Live logs for Iveras
sudo ./start-server logs-sf      # Live logs for SpiderFoot
sudo ./start-server update       # Git pull + pip install + restart
```

Or use systemd directly:

```bash
sudo systemctl status osint-dashboard
sudo systemctl status spiderfoot
sudo journalctl -u osint-dashboard -f
```

---

### Option B: Manual Setup (Development / macOS)

#### Prerequisites

- Python 3.12+
- Node.js 18+ (for frontend build)
- PostgreSQL (optional — SQLite fallback if `DATABASE_URL` not set)
- Git

#### Steps

```bash
# 1. Clone the repository (saas-migration branch)
git clone -b saas-migration https://github.com/mail2jack/osint-dashboard.git
cd osint-dashboard

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install production dependencies
pip install -r requirements.txt

# 4. Install development dependencies (optional, for testing/linting)
pip install -r requirements-dev.txt

# 5. Build frontend assets
npm install
node build.mjs

# 6. Install Playwright browsers (for PDF/screenshot features)
playwright install chromium

# 7. Copy environment file
cp .env.example .env
# Edit .env — at minimum set CMS_ENCRYPTION_KEY and FLASK_ENV=development

# 8. Run the application
python app.py
```

The application will be available at: `http://localhost:5000`

For PostgreSQL, add `DATABASE_URL=postgresql://user:pass@localhost/dbname` to `.env`.

### Default Login

- **Username:** admin
- **Password:** changeme123 (change on first login!)

---

## Getting Started

1. **Log in** with your credentials
2. **Configure Settings** (⚙️ Settings → API Keys) - Add Brave Search API key for OSINT
3. **Create a Client** - Add your first client
4. **Create a Case** - Start a new investigation case
5. **Add Subjects** - Add persons, companies, or vehicles to investigate

---

## Dashboard

The dashboard provides an overview of your work:

- **Active Cases** - Cases currently in progress
- **Recent Activity** - Latest updates across all cases
- **Reminders** - Upcoming follow-ups
- **Quick Stats** - Case count, subject count, recent searches

---

## Cases

Cases are the central unit of organization for investigations.

### Creating a Case

1. Go to **Cases** → **Create Case**
2. Fill in:
   - **Title** - Case name
   - **Case Number** - Auto-generated or custom
   - **Client** - Select from clients
   - **Priority** - Low/Medium/High/Critical
   - **Status** - Open/In Progress/On Hold/Closed
   - **Description** - Case details

### Case View

Each case shows:
- **Overview** - Case details and status
- **Subjects** - Linked subjects
- **Findings** - Evidence and discoveries
- **Comments** - Team notes and updates
- **Financial Records** - Related transactions
- **Screenshots** - Visual evidence
- **OSINT Search** - Run searches linked to this case

### Reopening Cases

Closed cases can be reopened via **Edit** → **Reopen Case**

---

## Clients

Clients are organizations or individuals who commission investigations.

### Fields

- **Name** - Company or individual name
- **Contact Person** - Primary contact
- **Email / Phone** - Contact details
- **Address** - Structured fields (Street, Number, Zipcode, Town, Country) with 🔍 postcode check button
- **Contract Number** - Reference number
- **Is Company** - Toggle for company/individual
- **Is Active** - Active/inactive status

### Postcode Check

Both client and subject address forms have a **🔍 button** next to the zipcode field:
1. Enter **Zipcode** + **Number**
2. Click **🔍**
3. Calls the PDOK BAG API
4. Street and Town are auto-filled from the national registry

### Politiebureau Lookup

On the client and subject view pages, each address has a **🚔 Politiebureau** button that:
1. Looks up the address in the BAG registry to get coordinates
2. Calls `api.politie.nl/politiebureaus/v1` with those coordinates
3. Displays the nearest police station: name, address, phone, opening hours, OSM map link

---

## Subjects

Subjects are entities being investigated: persons, companies, vehicles, etc.

### Subject Types

| Type | Description |
|------|-------------|
| Person | Individual persons |
| Company | Businesses and organizations |
| Organization | Non-profit or government |
| Vehicle | Cars, motorcycles, boats |
| Vessel | Ships and boats |
| Property | Real estate |

### Creating a Subject

1. Go to **Subjects** → **Create Subject**
2. **Select Type** - Choose the subject type
3. **Fill Details** - Type-specific fields appear
4. **Link to Case** - Optionally link to a case

### Subject Fields

**All Subjects:**
- Name
- Address(es) — structured: Street, Number, Zipcode, Town, Country (multiple per subject)
- Risk Score (0-100)
- Notes

**Persons:**
- Email
- Phone (with **📞 Check** button)
- ID/Passport Number

### Address Verification (Kadaster BAG)

Each subject can have multiple addresses. On the subject view page:
- Each address card has a **🏛 Kadaster** button
- Clicking it looks up the address in the PDOK BAG API (Dutch cadastre)
- Returns: status, type, purpose, surface area, year built, municipality, coordinates
- Verified addresses show a **✓ KADASTER** badge

### Address Form (Create/Edit)

Address forms use a 5-column grid with separate fields:
- **Street** — street name
- **Number** — house number (with addition)
- **Zipcode** — Dutch postal code
- **Town** — city/town
- **🔍** — postcode check button (fills street + town from zipcode + number)
- **Primary** checkbox — marks the primary address

### Phone Enrichment

Each person subject has a **📞 Check** button next to the phone number:
- Validates the number using the `phonenumbers` library
- Returns: formatted number, country, region, carrier, line type, timezone
- Checks WhatsApp and Telegram presence via web URLs
- Uses a free Dutch API (bedrijfsdata.nl) for NL number enrichment

### Interpol + Politie Check

Person subjects have a **🌍 Check Interpol** button that:
1. Searches INTERPOL **Red Notices** (wanted persons) by name
2. Searches INTERPOL **Yellow Notices** (missing persons)
3. Falls back to scraping `politie.nl/vermist` if Interpol is rate-limited
4. Displays matches with name, DOB, nationality, charge/description, and source URL

**Vehicles:**
- License Plate (Kenteken)
- VIN (Chassisnummer)
- Merk (Brand)
- Model (Handelsbenaming)
- RDW Data (automatic lookup)

### Subject Relationships

Subjects can be linked to each other:
- "Is related to"
- "Is associated with"
- "Is family of"
- Custom relationship types

---

## Search

### Global Search

Search across all data from the navigation bar:

- **Cases** - By title, case number, description
- **Clients** - By name, contact
- **Subjects** - By name, ID number
- **Findings** - By title, content
- **Comments** - By content
- **Notes** - By subject notes

### Filters

Use category filters to narrow results:
- All / Cases / Clients / Subjects / Findings / Comments / Notes

### Face Search

Find similar faces across all subjects:

1. Go to **Search**
2. Scroll to **Find Similar Faces**
3. Upload a photo
4. View matching subjects with similarity percentage

---

## OSINT Tools

### Running OSINT Search

1. Open a **Case**
2. Click **🔍 OSINT Search**
3. Enter a name or query
4. Results appear in real-time

### Search Sources

- **Brave Search** (primary) - Requires API key
- **DuckDuckGo** (fallback) - No API key needed

### DORK Categories

Enable OSINT dorks for:
- Social Media
- Documents
- Images
- People Search
- Leaks

### Adding Findings

After OSINT search:
1. Review results
2. Select relevant findings
3. Click **Add Selected to Findings**
4. Findings are saved to the case

---

## SpiderFoot Integration

SpiderFoot is een open-source OSINT tool die automatisch informatie verzamelt over domains, emailadressen, IPs, en meer. De Iveras CMS integreert met een lokale SpiderFoot instance.

### Setup

#### Productie (via install.sh)

Als je `install.sh` hebt gedraaid, is SpiderFoot al geïnstalleerd en geconfigureerd:

- SpiderFoot draait als **systemd service** (`spiderfoot.service`)
- Digest auth staat aan met een **random gegenereerd wachtwoord**
- Wachtwoord staat in `/opt/osint-dashboard/.env` (`SPIDERFOOT_PASSWORD`)
- Reverse proxy via Nginx op `http://<server>/spiderfoot/`
- Credentials staan automatisch in de Iveras database

#### Handmatig (development)

1. **Clone SpiderFoot**:
   ```bash
   git clone https://github.com/smicallef/spiderfoot.git
   cd spiderfoot
   pip3 install -r requirements.txt
   ```

2. **Maak een passwd file voor digest auth** (aanbevolen):
   ```bash
   mkdir -p ~/.spiderfoot
   echo "admin:jouw_wachtwoord" > ~/.spiderfoot/passwd
   chmod 600 ~/.spiderfoot/passwd
   ```

3. **Start SpiderFoot met auth**:
   ```bash
   python3 ./sf.py -l 127.0.0.1:5001
   ```
   (passwd file wordt automatisch geladen uit `~/.spiderfoot/passwd`)

4. **Configureer in Iveras**: Ga naar **SpiderFoot** → **Settings**
   - URL: `http://localhost:5001`
   - Username: `admin`
   - Password: `jouw_wachtwoord`

### Een Scan Starten

1. Ga naar **SpiderFoot → Start New Scan**
2. **Target** — typ een domain, email, IP, telefoonnummer, etc.
   - Het type wordt **automatisch herkend** (bv. `test@email.com` → EMAILADDR)
   - Of klik op een **Recent Target** om snel een eerder target te herscannen
3. Kies een **Investigation Profile** (Basic OSINT, Person, Company, etc.)
4. Kies **Scan Intensity** (Passive wordt aanbevolen)
5. Optioneel: **Link to Case** om resultaten later te importeren
6. Klik **Start Scan**

### Tijdens de Scan

- De pagina toont een **progress bar** die automatisch ververst
- Je krijgt een **browser notificatie** wanneer de scan klaar is
- Polling elke 10 seconden, geen volledige pagina refresh

### Resultaten Bekijken

Na voltooiing toont de scan pagina:

- **Result Summary** — aantal resultaten per type
- **Rich Result Cards** — elk resultaat heeft een type-specifiek icoon en kleur:
  - ✉ Email → blauw
  - 🌐 Domein → groen
  - 🌍 IP → paars
  - 📞 Telefoon → oranje
  - 👤 Naam → blauw
  - 🏷 Username → oranje
  - 🏢 Bedrijf → mint
  - 📍 Adres → rood
  - 🔴 Breach → donkerrood
  - ⚙ Technisch → grijs
- **📋 Copy-knop** — klik om een waarde te kopiëren
- **Filter balk** — filter resultaten op tekst
- **Account Cards** — social media accounts (Pinterest, Facebook, etc.) met platform-specifieke kleuren
- **Collapsible groepen** — klik op een type header om de groep open/dicht te klappen

### Resultaten Importeren in een Case

Als de scan gelinkt is aan een case, klik **Import to Case** om alle resultaten als findings toe te voegen.

### Alle Scans Bekijken

- **Dashboard** (`/cms/spiderfoot`) — recente scans met status badges
- **All Scans** (`/cms/spiderfoot/scans`) — volledige lijst met filter- en zoekmogelijkheden
- **Zoek op dashboard** — filter scans op naam, target of status

### Troubleshooting

| Probleem | Oplossing |
|----------|-----------|
| "SpiderFoot Not Connected" | Start SpiderFoot: `python3 sf.py -l 127.0.0.1:5001` |
| 401 Unauthorized | Check username/password in Iveras Settings |
| Scan start niet | Check of SpiderFoot draait en of de URL klopt |
| Geen resultaten | Kies een uitgebreider profile of scan intensity |

---

## Face Recognition

### Encoding a Face

1. Go to a **Subject** with a photo
2. Upload a photo (if not present)
3. Click **👤 Encode Face**
4. Face encoding is stored in the database

### Finding Similar Faces

1. Go to **Search** → **Find Similar Faces**
2. Upload a photo
3. System compares against all encoded subjects
4. Results show similarity percentage:
   - **>80%** - High confidence (green)
   - **60-80%** - Medium confidence (orange)
   - **<60%** - Low confidence (red)

### TinEye Integration

Search where a subject's photo appears online:

1. Go to a **Subject** with a photo
2. Click **🔍 TinEye Search**
3. Opens TinEye.com with the image

---

## Vehicle Data (RDW)

Dutch vehicle data from RDW Open Data API.

### Check RDW Data

1. Create/edit a **Vehicle** subject
2. Enter **License Plate** (Kenteken)
3. Click **Check RDW** or **Fetch RDW**
4. Vehicle data auto-fills:
   - Brand (Merk)
   - Model (Handelsbenaming)
   - Color (Kleur)
   - Doors, Seats
   - APK Status
   - WAM Insurance
   - And more...

### RDW Fields (23 total)

| Field | Description |
|-------|-------------|
| Kenteken | License plate |
| Merk | Brand |
| Handelsbenaming | Model name |
| Voertuigsoort | Vehicle type |
| Inrichting | Body type |
| Eerste Kleur | Primary color |
| Tweede Kleur | Secondary color |
| Aantal Deuren | Number of doors |
| Aantal Zitplaatsen | Number of seats |
| Cilinderinhoud | Engine displacement |
| Aantal Cilinders | Number of cylinders |
| Massa Ledig | Empty weight |
| Maximum Massa | Maximum weight |
| Vervaldatum APK | APK expiry date |
| WAM Verzekerd | Insurance status |
| Taxi Indicator | Taxi license |
| Export Indicator | Exported status |
| EU Categorie | European category |
| Zuinigheidsclassificatie | Efficiency rating |
| Catalogusprijs | List price |
| Datum Eerste Toelating | First registration |
| Type | Type code |
| Variant | Variant code |
| Uitvoering | Execution code |

---

## Reminders

Set follow-up reminders for cases and subjects.

### Creating a Reminder

1. Go to **Reminders** → **Create Reminder**
2. Fill in:
   - **Title** - What to do
   - **Due Date** - When
   - **Type** - Follow-up / Deadline / Meeting / Call / Other
   - **Priority** - Low / Medium / High
   - **Linked Entity** - Case or Subject (optional)

### Recurrence

Set recurring reminders:
- Daily
- Weekly
- Monthly
- Yearly

### Notifications

Reminders appear on:
- Dashboard
- Reminders list
- Linked case/subject view

---

## Settings

Configure the application via ⚙️ Settings (Admin only).

### Categories

**🔑 API Keys**
- Brave Search API Key
- PimEyes API Key (future)
- TinEye API Key (future)

**🔍 Search**
- Default search engine
- Search result limit
- Enable OSINT dorks

**⚙️ General**
- Case number prefix
- Default risk score
- Organization name
- **Update Check Repo** — GitHub repo (user/repo) for update notifications (leave empty to disable)

**🔒 Security**
- Session timeout
- Password change requirement

**📧 Email**
- SMTP server configuration

### Access

Settings are **Admin only** - regular users cannot access.

---

## User Management

Manage team members and access levels.

### Roles

| Role | Permissions |
|------|------------|
| **Admin** | Full access, settings, user management |
| **Senior Investigator** | Create/edit cases, subjects, OSINT |
| **Junior Investigator** | View cases, limited editing |

### Creating Users

1. Go to **Users** → **Create User** (Admin)
2. Fill in details
3. Assign role
4. User receives login credentials

---

## Two-Factor Authentication (2FA)

The system supports optional TOTP-based two-factor authentication using any authenticator app (Google Authenticator, Authy, 1Password, etc.).

### Enabling 2FA

1. Go to your **profile page** (click your name in the top-right corner)
2. Click **Enable 2FA**
3. **Scan the QR code** with your authenticator app, or manually enter the key
4. Enter the **6-digit verification code** from the app to confirm
5. **Save the 8 recovery codes** — each can be used once if you lose your phone

### Logging in with 2FA

1. Enter your **username + password** as usual
2. If 2FA is enabled, you are redirected to the **2FA verification page**
3. Enter the **6-digit code** from your authenticator app
4. Login completes after successful verification

### Recovery Codes

- **8 codes** in `XXXX-XXXX-XXXX` format, generated during setup
- **Each code can be used once** — after that it is consumed
- Use the **"Use a recovery code instead"** link on the verification page
- Store them securely (password manager, safe, etc.)

### Disabling 2FA

- **Yourself:** profile page → enter password → click **Disable 2FA**
- **Admin:** can reset 2FA for any user from that user's profile page

### Technical Details

- Implements **TOTP** (Time-based One-Time Password, RFC 6238)
- Codes are valid for **30 seconds** (with a ±1 period grace window)
- Recovery codes are stored as **SHA-256 hashes** — never in plaintext
- 2FA is **optional per user** — existing users without 2FA are unaffected

---

## Audit Log

Track all system activity for compliance.

### Logged Actions

- User logins/logouts
- Case creation and changes
- Subject additions
- OSINT searches
- Settings changes
- Document uploads

### Viewing Logs

Go to **Audit Log** (Admin) to see:
- Timestamp
- User
- Action
- Entity affected
- IP address

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `s` | Focus search |
| `/` | Focus search |
| `j` | Next item |
| `k` | Previous item |
| `Enter` | View selected |
| `?` | Show help |

---

## API Endpoints

### CMS Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/cms/` | Dashboard |
| GET | `/cms/cases` | List cases |
| POST | `/cms/cases/create` | Create case |
| GET | `/cms/cases/<id>` | View case |
| GET | `/cms/clients` | List clients |
| GET | `/cms/subjects` | List subjects |
| POST | `/cms/subjects/create` | Create subject |
| GET | `/cms/search` | Global search |
| GET | `/cms/api/search` | Search API |
| POST | `/cms/check-rdw-vehicle` | RDW lookup |
| POST | `/cms/subjects/compare-faces` | Face matching |
| POST | `/cms/api/kadaster-lookup` | Address verification via PDOK BAG |
| POST | `/cms/api/phone-lookup` | Phone number enrichment |
| POST | `/cms/check-policie-data` | Interpol Red/Yellow Notice check |
| GET | `/cms/check-policie-data-status` | Interpol API status |
| POST | `/cms/api/politiebureau-lookup` | Nearest police station lookup |
| GET | `/cms/api/check-update` | Check for newer version on GitHub |

### OSINT Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/cms/cases/<id>/osint-search` | Start OSINT search |
| GET | `/cms/osint-search/<id>/status` | Search status |
| POST | `/cms/cases/<id>/osint-search/add-findings` | Add findings |

---

## Troubleshooting

### Common Issues

**"Session expired"**
- Log in again
- Check session timeout in Settings

**"API key invalid"**
- Verify Brave API key in Settings
- Free tier available at brave.com/search/api

**"Face recognition not working"**
- Ensure internet connection (loads models)
- Try a clearer photo with visible face
- Check browser console for errors

**"RDW lookup failed"**
- RDW API may be temporarily unavailable
- Try again later
- Enter data manually

**"PDF export not working"**
- Check reportlab is installed: `pip install reportlab`

**"SpiderFoot 401 Unauthorized"**
- SpiderFoot auth staat aan maar credentials kloppen niet
- Check `~/.spiderfoot/passwd` en Iveras Settings
- Na install.sh: check `/opt/osint-dashboard/.env` voor `SPIDERFOOT_PASSWORD`
- Herstel settings in PostgreSQL: `Setting.set('spiderfoot_password', '<pass>')`

**"SpiderFoot start niet (PermissionError: /home/osint)"**
- `ProtectHome=true` in systemd service blokkeert toegang — verwijder deze regel
- Fix: `sudo sed -i '/ProtectHome=true/d' /etc/systemd/system/spiderfoot.service && sudo systemctl daemon-reload && sudo systemctl restart spiderfoot`

**"SpiderFoot start niet (unrecognized arguments: --passwd)"**
- SpiderFoot 4.0 heeft `--passwd` flag verwijderd — de passwd file wordt automatisch geladen
- Fix: `sudo sed -i 's/ --passwd [^ ]*//' /etc/systemd/system/spiderfoot.service && sudo systemctl daemon-reload && sudo systemctl restart spiderfoot`

**"Kadaster lookup not found"**
- Alleen Nederlandse adressen worden ondersteund (PDOK BAG)
- Controleer postcode formaat (1234 AB) en huisnummer

**"Phone lookup failed"**
- Alleen Nederlandse nummers worden uitgebreid geënrich
- Basisvalidatie werkt voor alle landen via phonenumbers library
- WhatsApp/Telegram check vereist internettoegang

**"Interpol rate limited"**
- Akamai CDN blokkeert na ~5-10 requests
- Wacht een paar minuten of gebruik de politie.nl/vermist fallback
- De politie.nl scraping is stabieler maar minder uitgebreid

**"Politiebureau not found"**
- Alleen Nederlandse politiebureaus worden opgehaald
- Het adres moet geldige coördinaten hebben (via PDOK BAG of kadaster_data)

**"Update check fails"**
- Zet `update_check_repo` in Settings (bv. `mail2jack/osint-dashboard`)
- De app checkt via raw.githubusercontent.com of er een nieuwere VERSION is
- Resultaat wordt 1 uur gecachet

**"Health check fails"**
- Run: `curl http://localhost:5000/health`
- Verwacht: `{"status":"ok","database":"connected","spiderfoot":"connected"}`
- Check logs: `journalctl -u osint-dashboard -n 50`

**"502 Bad Gateway" (via Nginx)**
- Iveras of SpiderFoot is gestopt
- Check: `systemctl status osint-dashboard spiderfoot`

### Upgrading from v3.3 to v3.4 (PostgreSQL migration)

If you upgrade from an older version that used SQLite (`cms.db`), settings stored in the old SQLite database (like SpiderFoot credentials) are **not** automatically migrated to PostgreSQL.

After upgrading, re-save the SpiderFoot settings:

```bash
cd /opt/osint-dashboard
sudo -u osint ./venv/bin/python3 -c "
import os
os.environ['FLASK_APP'] = 'app.py'
from app import app
from cms.models import Setting
with app.app_context():
    Setting.set('spiderfoot_url', 'http://127.0.0.1:5001')
    Setting.set('spiderfoot_username', 'admin')
    Setting.set('spiderfoot_password', '<password>')
    print('Settings migrated to PostgreSQL')
"
```

The SpiderFoot password is in `/opt/osint-dashboard/.env` (`SPIDERFOOT_PASSWORD`) and `/home/osint/.spiderfoot/passwd`.

---

### Database Issues

If you see database errors:

```bash
# Check which database is being used
grep DATABASE_URL /opt/osint-dashboard/.env

# With PostgreSQL: check connection
sudo -u postgres psql -d osint_db -c "\dt"

# Reset SQLite (WARNING: loses all data)
rm cms.db
python app.py
```

### Clear Cache

```bash
# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
find . -name "*.pyc" -delete
```

### Clean Up Logs

Log files (`spiderfoot.log`, `app.log`) groeien na verloop van tijd. Logrotate ruimt ze automatisch op, maar je kunt ook handmatig leegmaken:

```bash
# Vanuit de monitor directory:
: > spiderfoot.log && : > app.log
```

---

## Changelog

### Version 3.4.0 — May 2026

**PostgreSQL & Production Stability:**
- App now respects `DATABASE_URL` env var — `install.sh` sets up PostgreSQL by default, falls back to SQLite if unset
- `func.instr()` replaced with dialect-agnostic variant (SQLite `instr` / PostgreSQL `strpos`)
- `install.sh` accepts space-separated domain list for multi-domain SSL (e.g. `joost.iveras.nl joost.iveras.com`)
- Merged Nginx templates into one with dynamic `server_names` substitution
- Certbot registers all domains in a single certificate
- STEP 13 (`db.create_all()`) runs as `osint` user instead of root, preventing file ownership issues
- `start-server update` now reinstalls Python packages

**Python 3.14 Compatibility:**
- System `python3-lxml` (apt) is copied into each venv before pip install
- SpiderFoot's `lxml<5` pin is removed from `requirements.txt` after clone to prevent source-build failures
- Swap file auto-created to prevent OOM during dependency installation

**SpiderFoot v4 Fixes:**
- Removed `ProtectHome=true` from systemd service (blocked write access to `/home/osint`)
- Removed deprecated `--passwd` flag from service ExecStart (removed in SF 4.0 — passwd file is auto-loaded)
- `start.sh` uses SpiderFoot venv Python and correct database path

**Update Notifications:**
- `install.sh` now auto-sets `update_check_repo` in the database — banner works out-of-the-box
- Fixed JS fetch to send `Accept: application/json` header so the API isn't redirected to login
- Fixed `unauthorized` handler to also check `Accept` header for API detection
- Fixed `do-update` endpoint: uses full paths for git/systemctl, fixed db migration call
- Version bumped to 3.4.0 (VERSION file + version.py synced)

**Bug Fixes:**
- Root redirect `/` → `/cms/dashboard` (removed duplicate `index` endpoint)
- `requirements.txt`: `lxml` unpinned to prefer wheels over source builds

---

### Version 3.3.0 - May 2026

**Two-Factor Authentication:**
- TOTP-based 2FA met authenticator app (Google Authenticator, Authy, 1Password)
- QR-code setup met verificatie-stap
- 8 eenmalige recovery codes (gehasht opgeslagen)
- Login-flow met 2FA-check na wachtwoord-verificatie
- Optioneel per gebruiker — geen impact op bestaande accounts
- Admin kan 2FA resetten voor andere gebruikers
- 2FA-status zichtbaar in navigatiebalk (groen ✓-icoon)

**Migraties:**
- Automatische `ALTER TABLE` migraties voor nieuwe kolommen (`totp_secret`, `totp_enabled`, `backup_codes` op users; `social_media_ids`, `rdw_data`, `face_encoding` op subjects)

---

### Version 3.2.0 - May 2026

**Production Deployment:****
- `install.sh` v3.0 — one-command server setup voor Ubuntu/Debian
- Automatische SpiderFoot installatie met digest auth (random password)
- PostgreSQL random password generatie (i.p.v. hardcoded)
- `CMS_ENCRYPTION_KEY` auto-generatie in `.env`
- Nginx reverse proxy met `/spiderfoot/` route en SSL via certbot
- Systemd services voor zowel Iveras als SpiderFoot
- `/health` endpoint voor monitoring (DB + SpiderFoot status)
- `.env.example` met alle configuratie variabelen gedocumenteerd

### Version 3.1.0 - May 2026

**New Features:**
- SpiderFoot OSINT Integration — scan domains, emails, IPs, phones, etc.
- SpiderFoot Dashboard met scan cards, status badges, real-time progress
- Rich result cards met type-specifieke iconen/kleuren per resultaat
- Copy-to-clipboard buttons op alle resultaten
- Auto-detect target type bij starten scan (email → EMAILADDR, etc.)
- Recent targets quick-select knoppen
- Auto-refresh met browser notificaties tijdens running scans
- Account cards met platform-specifieke kleuren voor social media resultaten
- Filter balk op resultaten en scan lijsten
- SpiderFoot password auth ondersteuning (digest auth)
- Scan subject pagina met type-specifieke accentkleuren

**Improvements:**
- Alle spiderfoot pagina's naar card-style layout (i.p.v. tabellen)
- Stats row compacter gemaakt (Open, Active, Suspended, Closed, etc.)
- SFURL parsing met HTML entity unescaping in resultaten
- Error handling — try/except rond SpiderFoot API calls
- Dashboard search voor scans
- Logrotate config voor log management

### Version 3.0.0 - April 2026

**New Features:**
- Global Search across all entities
- Face Recognition with face-api.js
- TinEye integration for image search
- Settings Configuration UI (Admin)
- Type-first Subject creation
- Enhanced Vehicle/RDW fields (23 fields)
- Subject Relationships (bidirectional)
- Findings Filter and badges
- Reminders System
- Audit Logging improvements
- Case Reopen functionality
- Screenshot Management
- Social Media ID Extraction

**Improvements:**
- New Dashboard design
- Enhanced navigation
- Better mobile support
- Dark mode toggle

### Version 2.1.0 - March 2026

- Added Webcams tool
- Platform selector for Email tool
- Improved detection logic
- Time estimates for progress
- Cancel/stop functionality

### Version 2.0.0

- Major UI redesign
- Combined Sherlock + Maigret
- Added Holehe tool
- Confidence scoring
- Real-time streaming

### Version 1.0.0

- Initial release
- Core OSINT tools
- Search history
- PDF export

---

## Security Considerations

- Use responsibly and legally
- Respect privacy and platform terms of service
- Do not use for harassment or illegal activities
- Results may contain false positives
- Some APIs block automated requests
- Follow GDPR compliance for personal data
- Regularly review audit logs
- Use strong passwords and change defaults

---

## Support

For issues and feature requests:
- GitHub Issues: https://github.com/mail2jack/osint-dashboard/issues

---

*Iveras OSINT Case Management System v3.6.0 — July 2026*
