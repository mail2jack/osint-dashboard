# Iveras OSINT Manual

**Version:** 2.1.2  
**Last Updated:** March 2026

---

## Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Getting Started](#getting-started)
4. [Tools Overview](#tools-overview)
5. [Detailed Tool Usage](#detailed-tool-usage)
   - [People Search](#people-search)
   - [Social Media Search](#social-media-search)
   - [Email Lookup](#email-lookup)
   - [Username Search (Sherlock)](#username-search-sherlock)
   - [Holehe](#holehe)
   - [Maigret](#maigret)
   - [Phone Lookup](#phone-lookup)
   - [IP Lookup](#ip-lookup)
   - [Domain Lookup](#domain-lookup)
   - [Webcams](#webcams)
   - [Antisocial](#antisocial)
6. [Interface Features](#interface-features)
7. [Search History & Archive](#search-history--archive)
8. [System Controls](#system-controls)
9. [Keyboard Shortcuts](#keyboard-shortcuts)
10. [API Endpoints](#api-endpoints)
11. [Troubleshooting](#troubleshooting)
12. [Changelog](#changelog)

---

## Introduction

Iveras OSINT is a comprehensive open-source intelligence gathering tool for security researchers, investigators, and privacy-conscious users.

**Key Features:**
- Search across 50+ social media platforms
- People search via DuckDuckGo with 24 advanced queries
- Email OSINT with Sherlock + Holehe combination
- Username discovery using Sherlock and Maigret
- Phone number lookup (WhatsApp, Telegram, Carrier info)
- IP and domain intelligence
- Live webcam directory by country/city
- Real-time progress with time estimates
- Search history with archiving
- Results sorted by confidence scores
- Export results to PDF

---

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Setup

1. Clone or download the repository:
```bash
git clone https://github.com/mail2jack/osint-dashboard.git
cd osint-dashboard
```

2. Create a virtual environment (recommended):
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

### Running the Application

```bash
python app.py
# or
./start.sh
```

The dashboard will be available at: `http://localhost:5000`

---

## Installation by Platform

### macOS

**Prerequisites:**
- Python 3.8+ (check: `python3 --version`)
- Git (check: `git --version`)

**Installation:**
```bash
# 1. Clone the repository
git clone https://github.com/mail2jack/osint-dashboard.git
cd osint-dashboard

# 2. Create virtual environment
python3 -m venv venv

# 3. Activate virtual environment
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the application
python app.py
```

**Alternative - Using Homebrew:**
```bash
# Install Python if needed
brew install python3 git

# Then follow steps 1-5 above
```

**To deactivate virtual environment:**
```bash
deactivate
```

---

### Linux

**Prerequisites:**
- Python 3.8+ (check: `python3 --version`)
- Git (check: `git --version`)

**Installation (Ubuntu/Debian):**
```bash
# 1. Install prerequisites
sudo apt update
sudo apt install python3 python3-venv git

# 2. Clone the repository
git clone https://github.com/mail2jack/osint-dashboard.git
cd osint-dashboard

# 3. Create virtual environment
python3 -m venv venv

# 4. Activate virtual environment
source venv/bin/activate

# 5. Install dependencies
pip install -r requirements.txt

# 6. Run the application
python app.py
```

**Installation (Fedora/RHEL):**
```bash
# 1. Install prerequisites
sudo dnf install python3 python3-pip git

# 2-6. Follow steps 2-6 above
```

**Installation (Arch/Manjaro):**
```bash
# 1. Install prerequisites
sudo pacman -S python python-pip git

# 2-6. Follow steps 2-6 above
```

**To deactivate virtual environment:**
```bash
deactivate
```

---

### Windows

**Prerequisites:**
- Python 3.8+ ([Download from python.org](https://www.python.org/downloads/))
- Git ([Download from git-scm.com](https://git-scm.com/download/win))

**Installation:**
```powershell
# 1. Open PowerShell or Command Prompt

# 2. Clone the repository
git clone https://github.com/mail2jack/osint-dashboard.git
cd osint-dashboard

# 3. Create virtual environment
python -m venv venv

# 4. Activate virtual environment
.\venv\Scripts\activate

# 5. Install dependencies
pip install -r requirements.txt

# 6. Run the application
python app.py
```

**Using Command Prompt (CMD):**
```cmd
# Same commands as PowerShell
```

**Note:** If you get a "script execution" error, run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**To deactivate virtual environment:**
```powershell
deactivate
```

---

## Quick Reference Card

| Step | macOS/Linux | Windows |
|------|-------------|---------|
| Clone | `git clone ...` | `git clone ...` |
| Create venv | `python3 -m venv venv` | `python -m venv venv` |
| Activate | `source venv/bin/activate` | `.\venv\Scripts\activate` |
| Install | `pip install -r requirements.txt` | `pip install -r requirements.txt` |
| Run | `python app.py` | `python app.py` |
| Deactivate | `deactivate` | `deactivate` |

---

## Troubleshooting Installation

### Python not found
- **macOS:** Install via Homebrew: `brew install python3`
- **Linux:** Use your package manager (apt, dnf, pacman)
- **Windows:** Download from [python.org](https://www.python.org/downloads/)

### Git not found
- **macOS:** `brew install git`
- **Linux:** `sudo apt install git` (Ubuntu) or use your package manager
- **Windows:** Download from [git-scm.com](https://git-scm.com/download/win)

### pip not found
- **macOS:** `python3 -m ensurepip` or `brew install python3`
- **Linux:** `sudo apt install python3-pip`
- **Windows:** Reinstall Python with pip included

### Permission denied errors
- **Linux:** Use `sudo` with pip: `sudo pip install...`
- **macOS/Linux:** Always use virtual environments (avoids sudo issues)
- **Windows:** Run PowerShell as Administrator

### SSL/Certificate errors
```bash
# macOS
/Applications/Python\ 3.x/Install\ Certificates.command

# Linux
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| flask | Web framework |
| requests | HTTP client |
| httpx | Async HTTP client |
| holehe | Email OSINT |
| sherlock-project | Username OSINT |
| maigret | Deep username search |
| phonenumbers | Phone number parsing |
| reportlab | PDF generation |

### Running the Application

```bash
python app.py
# or
./start.sh
```

The dashboard will be available at: `http://localhost:5000`

---

## Getting Started

1. **Launch the app**: Run `python app.py`
2. **Open browser**: Navigate to `http://localhost:5000`
3. **Select tool**: Click on a tool in the toolbar
4. **Enter query**: Type your search term
5. **Start search**: Click "Search" or press Enter
6. **View results**: Results show sorted by confidence with time estimates

---

## Tools Overview

### Toolbar Tools

| Tool | Description | Search Type |
|------|-------------|-------------|
| **People** | Name search via DuckDuckGo | Full Name |
| **Social** | Multi-platform username/email search | Username/Email |
| **Email** | Sherlock + Holehe combined | Email Address |
| **Phone** | WhatsApp/Telegram/Carrier info | Phone Number |
| **Webcams** | Live webcam directory | Country/City |
| **Maigret** | Deep username scanner | Username |
| **Sherlock** | Fast username finder | Username |
| **Holehe** | Email deep search | Email Address |
| **IP** | IP geolocation and intel | IP Address |
| **Domain** | WHOIS and DNS | Domain Name |
| **AntiSocial** | 3-tier verification username search | Username |

---

## Detailed Tool Usage

### People Search

Search for individuals by full name using DuckDuckGo with advanced Google dorking queries.

**How to use:**
1. Select "People" tool
2. Enter full name (e.g., "John Smith")
3. Click "Search"

**Search Features:**
- 24 advanced dork queries covering:
  - Username patterns (e.g., "johnsmith", "john_smith", "johnsmith37")
  - Social media sites (Facebook, Twitter, Instagram, LinkedIn, TikTok, etc.)
  - Dating apps (Tinder, Bumble)
  - Professional sites (GitHub, Reddit, Pinterest)
  - File searches (PDF, DOC)
  - Email discovery

**Results Display:**
- Sorted by confidence score (social media first)
- Shows search links for manual verification
- Time estimate and search duration

**Search Links Generated:**
- Google, DuckDuckGo, LinkedIn, Facebook, Twitter/X, GitHub, Instagram, Reddit, YouTube, TikTok, Pipl, Truecaller

---

### Social Media Search

Comprehensive username/email/phone search across 50+ platforms with improved detection.

**How to use:**
1. Select "Social" tool
2. Enter username, email, or phone number
3. Select platform categories (optional)
4. Click "Search"

**Platform Categories:**
- **Quick (~30)** - Top 30 most popular sites
- **Standard (~50)** - Extended coverage
- **Deep (~100)** - Most comprehensive
- **Full (~200)** - All available sites

**Detection Features:**
- HTTP status code analysis
- Login redirect detection
- Username-in-URL verification
- Content pattern matching

**Results Display:**
- Found accounts (green) - sorted first
- Unknown status (yellow)
- Not found (gray) - sorted last
- Confidence badges (High/Med/Low)

---

### Email Lookup

Combined Sherlock + Holehe for comprehensive email OSINT.

**How to use:**
1. Select "Email" tool
2. Enter email address
3. Click "Search"

**Search Options:**
- Quick (~30 sites)
- Standard (~50 sites)
- Deep (~100 sites)
- Full (~200 sites)

**Tools Used:**
- **Sherlock**: Checks username patterns
- **Holehe**: Checks registration via password reset flows

**Privacy Note:** Holehe only examines password reset responses - it does NOT test passwords or access accounts.

---

### Username Search (Sherlock)

Fast username search across popular platforms.

**How to use:**
1. Select "Sherlock" tool
2. Enter username
3. Click "Search"

**Features:**
- Real-time progress with time estimates
- Cancel button to stop early
- Results sorted by confidence

---

### Holehe

Deep email search using registration detection.

**How to use:**
1. Select "Holehe" tool
2. Enter email address
3. Click "Search"

**Method:**
- Tests password reset flows on 100+ sites
- Detects if email is registered based on response patterns

---

### Maigret

Deep username scanner using ranked database.

**How to use:**
1. Select "Maigret" tool
2. Enter username
3. Click "Search"

**Features:**
- Sites ranked by popularity
- Comprehensive coverage
- Cross-validation with other tools

---

### Phone Lookup

Multi-service phone intelligence.

**How to use:**
1. Select "Phone" tool
2. Enter phone in international format (e.g., `+31612345678`)
3. Click "Search"

**Input Formats:**
- International: `+31612345678`
- With country code: `31612345678`
- With zeros: `06-12-34-56-78`

**Services Checked:**
| Service | Status |
|---------|--------|
| **WhatsApp** | Check blocked by API limitations |
| **WhatsApp (2Chat)** | Enhanced with API key (see below) |
| **Telegram** | Check blocked by API limitations |
| **Carrier** | Network operator info |
| **Country** | From phone number prefix |
| **Timezone** | From phone number |

---

### Phone Lookup - 2Chat API Integration

For detailed WhatsApp information, you can enable the 2Chat API integration.

**Setup:**

1. Create account at [2chat.co](https://2chat.co)
2. Get your API key from [app.2chat.io/api](https://app.2chat.io/api)
3. Connect your WhatsApp number via QR code in the 2Chat dashboard
4. Set environment variables:

```bash
# Linux/macOS
export TWOCHAT_API_KEY="your-api-key"
export TWOCHAT_WHATSAPP_NUMBER="+1234567890"

# Windows (PowerShell)
$env:TWOCHAT_API_KEY="your-api-key"
$env:TWOCHAT_WHATSAPP_NUMBER="+1234567890"
```

**What you get with 2Chat:**
- Profile picture URL
- Business account information (name, description, website)
- Verified badge level (0-2)
- Status text
- Number ID (for programmatic messaging)
- Region and timezone from phone number

**Pricing:**
- Trial: 10 requests/min, 100 checks max
- Paid: 50 requests/min per connected number

**Note:** WhatsApp and Telegram basic checks are blocked by API limitations. Carrier and location info should still work.

---

### IP Lookup

IP address geolocation and threat intelligence.

**How to use:**
1. Select "IP" tool
2. Enter IP address
3. Click "Search"

**Information Retrieved:**
- Geolocation (city, region, country, coordinates)
- ISP and organization
- ASN information
- Timezone

---

### Domain Lookup

Domain registration and DNS information.

**How to use:**
1. Select "Domain" tool
2. Enter domain (e.g., `example.com`)
3. Click "Search"

**Information Retrieved:**
- Registrar, creation date, expiration
- DNS records (A, AAAA, MX, TXT, NS)
- SSL certificate details

---

### Webcams

Live webcam directory organized by country and city.

**How to use:**
1. Select "Webcams" tool
2. Enter country or city name (e.g., "Netherlands" or "Amsterdam")
3. Click "Search"

**Features:**
- 15 countries supported
- 47+ webcam locations
- Click country tags to filter
- Open webcams in browser

**Supported Countries:**
United States, Netherlands, UK, France, Germany, Japan, Australia, Italy, Spain, Canada, Switzerland, Austria, Belgium, Norway, Sweden

**Note:** Most webcams link to official tourism/city websites. True live streaming requires API keys from services like EarthCam.

---

### Antisocial

Advanced username search using 3-tier verification for reduced false positives.

**How to use:**
1. Select "AntiSocial" tool
2. Enter username
3. Optionally enable "Deep Search" for 500+ platforms
4. Click "Search"

**Setup Required:**

Antisocial runs as a separate service. Install it first:

```bash
git clone https://github.com/lukeslp/antisocial.git
cd antisocial
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python run.py
```

Set the URL in Iveras OSINT:
```bash
export ANTISOCIAL_URL="http://localhost:8000"
```

**Verification Tiers:**
| Tier | Method | Confidence |
|------|--------|------------|
| 1 | Official APIs | ~95% |
| 2 | Browser Automation | ~85% |
| 3 | HTTP Content Analysis | ~70% |

**Features:**
- False positive rate reduced to ~5% (vs 30-40% for basic checkers)
- 30+ platforms by default
- 500+ platforms with Deep Search (WhatsMyName)
- Real-time streaming results
- Confidence scores per account

**Note:** Requires Antisocial service running separately on port 8000.

---

## Interface Features

### System Status Indicator

- **Green (Idle)**: No search running
- **Red (Searching)**: Active search in progress

### Progress Display

During searches:
- Progress bar with percentage
- Current site being checked
- Time estimate remaining
- Cancel button to stop early

### Results Display

- Stats bar (Found, Checked, Time)
- Results sorted by confidence
- Copy URL buttons
- Click to open in new tab
- Confidence badges (High/Med/Low)

### Close Results

Click the close button (X) or the "Close" button to:
- Cancel any running search
- Clear results
- Reset the UI

---

## Search History & Archive

Access via the "History" button in the header.

**Features:**
- Last 50 searches with results
- Archive older searches
- Filter by tool type
- Mark as read

---

## System Controls

### Restart
Restarts the Flask server (keeps running).

### Exit
Stops and exits the application.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Start search |
| `Esc` | Close results |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/social/stream` | Social media search |
| POST | `/api/person/stream` | People search |
| POST | `/api/email/stream` | Email search |
| POST | `/api/email/holehe` | Holehe email search |
| POST | `/api/username/stream` | Sherlock username search |
| POST | `/api/username/maigret` | Maigret username search |
| POST | `/api/phone` | Phone lookup |
| POST | `/api/webcam` | Webcam directory |
| POST | `/api/ip` | IP lookup |
| POST | `/api/domain` | Domain lookup |
| GET | `/api/history` | Search history |
| GET | `/api/version` | Version info |

### Example

```bash
curl -X POST http://localhost:5000/api/username/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "testuser", "tags": ["30"]}'
```

---

## Troubleshooting

### Common Issues

**"Could not load Sherlock site data"**
- Ensure internet connection on first run
- Sherlock caches site data automatically

**"Could not load Maigret database"**
- Database downloads on first run
- Requires internet connection

**Slow Performance**
- Check internet connection
- Some platforms may rate-limit
- Use smaller search scope (Quick instead of Full)

**Phone/WhatsApp/Telegram Not Working**
- APIs often block automated requests
- This is a known limitation of OSINT tools
- Carrier info should still work
- For WhatsApp: Enable 2Chat API for detailed results (see Phone Lookup section)

### Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| Network timeout | Platform not responding | Try again later |
| Rate limited | Too many requests | Wait, use smaller scope |
| Invalid input | Wrong format | Check input requirements |
| 400 Bad Request | Wrong tool for input | Select correct tool |

---

## Changelog

### Version 2.1.2
- Added Antisocial integration (3-tier verification username search)
- New environment variable: `ANTISOCIAL_URL`
- Antisocial runs as separate service for reduced false positives

### Version 2.1.1
- Added 2Chat API integration for enhanced WhatsApp phone lookups
- New environment variables: `TWOCHAT_API_KEY`, `TWOCHAT_WHATSAPP_NUMBER`
- WhatsApp lookup now includes profile pic, business info, verified status (when 2Chat configured)

### Version 2.1.0
- Added Webcams tool (47+ locations, 15 countries)
- Added platform selector to Email tool (Quick/Standard/Deep/Full)
- Improved social search detection logic
- Added time estimates to progress display
- Added cancel/stop functionality
- Results sorted by confidence scores
- People search: 24 dork queries (was 12)
- Improved platform detection for social media
- Removed debug console logs

### Version 2.0.0
- Major UI redesign with toolbar
- Combined Sherlock + Maigret tool
- Added Holehe tool
- Added confidence scoring
- Real-time streaming results
- System status indicator

### Version 1.0.0
- Initial release
- Core OSINT tools
- Search history
- PDF export

---

## Technical Details

### Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │────▶│    Flask    │────▶│   Tools     │
│  (HTML/CSS) │◀────│   Server    │◀────│ (OSINT Libs)│
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │  Cache &    │
                    │  History    │
                    └─────────────┘
```

### Libraries Used

| Library | Purpose |
|---------|---------|
| Flask | Web framework |
| Sherlock | Username finding |
| Holehe | Email OSINT |
| Maigret | Deep username search |
| httpx | Async HTTP |
| phonenumbers | Phone parsing |

---

## Security Considerations

- Use responsibly and legally
- Respect privacy and platform terms of service
- Do not use for harassment or illegal activities
- Results may contain false positives
- Some APIs block automated requests

---

*Iveras OSINT v2.1.2 - March 2026*
