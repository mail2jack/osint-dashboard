# Iveras OSINT Case Management System - Manual

**Version:** 3.1.0  
**Last Updated:** May 2026

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
16. [Audit Log](#audit-log)
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

---

## Installation

### Option A: One-command Server Install (Ubuntu/Debian — recommended)

```bash
sudo apt install -y wget
wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/master/install.sh
chmod +x install.sh
sudo ./install.sh
```

The script installs everything automatically:

- Python + pip + virtualenv
- PostgreSQL (random password generated)
- Nginx reverse proxy (Iveras + SpiderFoot routes)
- SpiderFoot (git clone + venv + digest auth)
- SSL via Let's Encrypt (optional, if domain provided)
- Systemd services (`osint-dashboard` and `spiderfoot`)
- UFW firewall (SSH/HTTP/HTTPS)
- Health check endpoint verification

During installation you'll be asked for a **domain name** (for SSL) or can press Enter for IP-only access.

After installation, edit the API keys in `/opt/osint-dashboard/.env` and the SpiderFoot password is shown in the install output.

#### Prerequisites

- **Ubuntu 20.04+** or **Debian 11+**
- Root access (sudo)
- Ports 80/443 open (or access to the server's IP)
- Optional: a domain name pointing to the server for SSL

### Option B: Manual Setup (Development / macOS)

#### Prerequisites

- Python 3.9+
- PostgreSQL (optional, SQLite default)
- Git

#### Steps

```bash
# 1. Clone the repository
git clone https://github.com/mail2jack/osint-dashboard.git
cd osint-dashboard

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment file
cp .env.example .env  # Edit .env with your API keys

# 5. Run the application
python app.py
```

The application will be available at: `http://localhost:5000`

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
- **Contract Number** - Reference number
- **Is Company** - Toggle for company/individual
- **Is Active** - Active/inactive status

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
- Address
- Risk Score (0-100)
- Notes

**Persons:**
- Email
- Phone
- ID/Passport Number

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
   python3 ./sf.py -l 127.0.0.1:5001 --passwd ~/.spiderfoot/passwd
   ```

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

**"Health check fails"**
- Run: `curl http://localhost:5000/health`
- Verwacht: `{"status":"ok","database":"connected","spiderfoot":"connected"}`
- Check logs: `journalctl -u osint-dashboard -n 50`

**"502 Bad Gateway" (via Nginx)**
- Iveras of SpiderFoot is gestopt
- Check: `systemctl status osint-dashboard spiderfoot`

### Database Issues

If you see database errors:

```bash
# Reset database (WARNING: loses all data)
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

### Version 3.2.0 - May 2026

**Production Deployment:**
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

*Iveras OSINT Case Management System v3.0.0 - April 2026*
