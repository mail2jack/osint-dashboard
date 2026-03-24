# OSINT Dashboard Manual

**Version:** 1.2.0  
**Last Updated:** March 2026

---

## Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Getting Started](#getting-started)
4. [Tools Overview](#tools-overview)
5. [Detailed Tool Usage](#detailed-tool-usage)
   - [Social Media Search](#social-media-search)
   - [People Search](#people-search)
   - [Email Lookup](#email-lookup)
   - [Username Search (Sherlock)](#username-search-sherlock)
   - [Maigret](#maigret)
   - [Phone Lookup](#phone-lookup)
   - [IP Lookup](#ip-lookup)
   - [Domain Lookup](#domain-lookup)
6. [AI Assistant](#ai-assistant)
   - [Setup](#setup)
   - [Natural Language Search](#natural-language-search)
   - [Result Summaries](#result-summaries)
   - [Profile Enrichment](#profile-enrichment)
7. [Interface Features](#interface-features)
8. [Search History & Archive](#search-history--archive)
9. [Platform Health Dashboard](#platform-health-dashboard)
10. [System Controls](#system-controls)
11. [Dark/Light Mode](#darklight-mode)
12. [Result Verification](#result-verification)
13. [Export Features](#export-features)
14. [Keyboard Shortcuts](#keyboard-shortcuts)
15. [API Endpoints](#api-endpoints)
16. [Troubleshooting](#troubleshooting)
17. [Changelog](#changelog)

---

## Introduction

OSINT Dashboard is a comprehensive open-source intelligence gathering tool that enables security researchers, investigators, and privacy-conscious users to discover online presence across multiple platforms.

**Key Features:**
- Search across 56+ social media platforms
- Email OSINT with false positive filtering
- Username discovery using Sherlock and Maigret
- Phone number lookup (WhatsApp, Telegram, Carrier)
- People search via search engines
- IP and domain intelligence
- **AI Assistant** with Ollama integration
- Dark/Light theme support
- Search history with archiving
- Platform health monitoring
- Export results to PDF

---

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Setup

1. Clone or download the repository:
```bash
git clone <repository-url>
cd monitor
```

2. Create a virtual environment (recommended):
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| flask | >=2.0.0 | Web framework |
| requests | >=2.25.0 | HTTP client |
| holehe | >=2.0.0 | Email OSINT |
| sherlock-project | >=0.14.0 | Username OSINT |
| reportlab | >=4.0.0 | PDF generation |
| maigret | >=0.5.0 | Extended username search |
| httpx | - | Async HTTP client (included with sherlock) |
| phonenumbers | - | Phone number parsing (included with holehe) |

### Running the Application

```bash
python app.py
```

The dashboard will be available at: `http://localhost:5000`

---

## Getting Started

1. **Launch the app**: Run `python app.py`
2. **Open browser**: Navigate to `http://localhost:5000`
3. **Select tool**: Click on a tool card in the "People OSINT" section
4. **Enter query**: Type your search term in the input field
5. **Start search**: Click the "Search" button or press Enter
6. **View results**: Expand results to see details and visit profiles

---

## Tools Overview

### People OSINT Section

| Tool | Description | Search Type | Platforms |
|------|-------------|-------------|-----------|
| **Social Media** | Multi-platform username/email/phone search | Mixed | 56 sites |
| **People Search** | Full name search via search engines | Name | 8 engines |
| **Email Lookup** | Combined Sherlock + Holehe | Email | 478+ sites |
| **Username Search** | Sherlock-based username finder | Username | 100 sites |
| **Maigret** | Deep username scanner | Username | 50 sites |
| **Phone Lookup** | WhatsApp/Telegram/Carrier info | Phone | 3 services |

### Network OSINT Section

| Tool | Description | Search Type |
|------|-------------|-------------|
| **IP Lookup** | IP geolocation and threat intel | IP Address |
| **Domain Lookup** | WHOIS and DNS records | Domain |

---

## Detailed Tool Usage

### Social Media Search

The Social Media tool provides comprehensive username/email/phone searches across 56 platforms.

**How to use:**
1. Select "Social Media" tool card
2. Enter a username, email, or phone number
3. (Optional) Select a site category from the dropdown
4. Click "Search"

**Site Categories:**
- **All Sites** (56): Search all available platforms
- **Social Media** (17): Facebook, Twitter/X, Instagram, LinkedIn, etc.
- **Developer** (9): GitHub, Stack Overflow, GitLab, etc.
- **Gaming** (4): Steam, Discord, Twitch, etc.
- **Creative** (12): YouTube, TikTok, Pinterest, etc.
- **Messaging** (4): WhatsApp, Telegram, Signal, etc.
- **Link-in-Bio** (2): Linktree, Carrd, etc.
- **Other** (8): Email services, forums, etc.

**Results Display:**
- Found accounts show in green with HTTP status
- Not found accounts show in gray with reduced opacity
- Rate-limited platforms show in orange
- Click any result to expand details

---

### People Search

Search for individuals by full name across multiple search engines.

**How to use:**
1. Select "People Search" tool card
2. Enter full name (e.g., "John Smith")
3. Click "Search"

**Search Engines:**
- Google
- Bing
- DuckDuckGo
- Yahoo
- Baidu
- Yandex
- Startpage
- Qwant

**Results:**
- Shows generated search URLs for each engine
- Click "Search Now" to open in new tab
- Results include query parameters for refined searches

---

### Email Lookup

Combined search using Sherlock (478 sites) and Holehe (121 sites) for comprehensive email OSINT.

**How to use:**
1. Select "Email Lookup" tool card
2. Enter email address (e.g., `user@example.com`)
3. Click "Search"

**Tools Used:**
- **Sherlock**: Checks if email appears in usernames/profiles
- **Holehe**: Checks password reset flows for registration

**Result Display:**
- Sherlock findings show with blue badge
- Holehe findings show with orange badge
- Results sorted by platform priority

**Privacy Note:**
Holehe works by examining password reset flows - it does NOT test passwords or access accounts.

---

### Username Search (Sherlock)

Fast username search across 100 popular platforms.

**How to use:**
1. Select "Username Search" tool card
2. Enter username (e.g., `johndoe`)
3. Click "Search"

**Features:**
- Fast parallel checking (100 sites)
- Progress bar with real-time updates
- Stop button to abort and show partial results
- Result verification to reduce false positives

**Verification Levels:**
- ✓ **Verified** (green): Username confirmed in response content
- ○ **Unconfirmed** (cyan): HTTP 200 but verification inconclusive
- ⚠ **Unverified** (orange): Likely false positive detected

---

### Maigret

Deep username scanner using 50 ranked sites.

**How to use:**
1. Select "Maigret" tool card
2. Enter username
3. Click "Search"

**Features:**
- Alternative to Sherlock with different site coverage
- Sites ranked by popularity
- Slower but potentially more comprehensive

**When to use:**
- If Sherlock doesn't find results
- For verification across multiple sources
- When Sherlock sites are rate-limiting

---

### Phone Lookup

Multi-service phone number intelligence.

**How to use:**
1. Select "Phone Lookup" tool card
2. Enter phone number in international format (e.g., `+31612345678`)
3. Click "Search"

**Services Checked:**

| Service | Information Retrieved |
|---------|---------------------|
| **WhatsApp** | Profile photo availability, last seen status |
| **Telegram** | User ID, username, bio, photos |
| **Carrier** | Network operator, country, line type |

**Input Format:**
- International: `+31612345678`
- With country code: `31612345678`
- With leading zeros: `06-12-34-56-78`

**Privacy Note:**
This tool only checks public profile information. It does NOT access private messages or contacts.

---

### IP Lookup

IP address geolocation and threat intelligence.

**How to use:**
1. Select "IP Lookup" tool card
2. Enter IP address (e.g., `8.8.8.8`)
3. Click "Search"

**Information Retrieved:**
- Geolocation (city, region, country)
- ISP and organization
- Coordinates and timezone
- ASN information
- Threat intelligence (if available)

---

### Domain Lookup

Domain registration and DNS information.

**How to use:**
1. Select "Domain Lookup" tool card
2. Enter domain (e.g., `example.com`)
3. Click "Search"

**Information Retrieved:**
- **WHOIS Data**: Registrar, creation date, expiration, owner
- **DNS Records**: A, AAAA, MX, TXT, NS records
- **SSL Info**: Certificate details (if HTTPS available)

---

## AI Assistant

The AI Assistant uses Ollama to provide intelligent features for your OSINT research.

### Setup

**Requirements:**
- Ollama installed (from https://ollama.com)
- Llama 3.2 model downloaded

**Installation:**
```bash
# Install Ollama (macOS)
brew install ollama

# Download the model
ollama pull llama3.2

# Start Ollama server
ollama serve
```

**Verification:**
1. Click the "AI" button in the header
2. You should see "AI is ready" with a green status

### Natural Language Search

Instead of typing exact queries, ask questions naturally.

**How to use:**
1. Click the "AI" button in the header
2. Type your question in the search box
3. Click "Analyze Query"

**Examples:**
| Natural Language | Detected Search |
|-----------------|-----------------|
| "Find anyone named John Smith in Amsterdam" | People Search |
| "Show me profiles for testuser123" | Username Search |
| "Check if john@example.com exists" | Email Lookup |
| "Look up phone number +31612345678" | Phone Lookup |

**Features:**
- Automatic tool detection
- Confidence scoring
- One-click search execution

### Result Summaries

After completing a search, get an AI-generated summary.

**How to use:**
1. Complete any search
2. Click the purple "AI Summary" button
3. View the summary in the AI panel

**Summary includes:**
- Overview of found accounts
- Patterns and commonalities
- Privacy recommendations

### Profile Enrichment

Get AI-generated insights about individual profiles.

**Coming soon:** Click on any found profile to get:
- Platform context (what the platform is typically used for)
- Privacy considerations
- Confidence scores

---

## Interface Features

### Header Actions

| Button | Function |
|--------|----------|
| **Light/Dark** | Toggle between light and dark theme |
| **AI** | Open AI Assistant panel |
| **Health** | Open platform health dashboard |
| **History** | Open search history panel |
| **Restart** | Restart the application server |
| **Exit** | Stop and exit the application |

### Search Progress

During searches, you'll see:
- **Progress bar**: Visual completion percentage
- **Stats**: Found count, checked count, total count
- **Current site**: Active platform being checked
- **Stop button**: Abort search and show partial results

### Result Expansion

Click any result to expand and see:
- Platform name
- Full profile URL
- HTTP status code
- Verification status
- Additional metadata (if available)

Click "Visit Profile" to open the profile in a new tab.

---

## Search History & Archive

### History Panel

Access via the "History" button in the header.

**Features:**
- **Recent Searches**: Last 50 searches with results
- **Archive**: Older searches for reference
- **Search/Filter**: Find specific searches
- **Tool Filter**: Filter by search type
- **Mark as Read**: Clear unread indicators

### Archive Management

- Click archive icon to move items from Recent to Archive
- "Mark All Read" button to clear unread badges
- Archived items persist across sessions

---

## Platform Health Dashboard

View the real-time status of all monitored platforms.

**Access:** Click the "Health" button in the header

**Status Categories:**

| Status | Color | Meaning |
|--------|-------|---------|
| **Working** | Green | Platform responding normally |
| **Degraded** | Orange | Slow responses or partial issues |
| **Blocked** | Red | Rate limiting or blocks detected |

**Features:**
- Overall health summary
- Per-platform status
- Last checked timestamp
- Refresh button to re-check

---

## System Controls

### Restart

Restarts the Flask application server while keeping it running.

**Use when:**
- Experiencing memory issues
- After installing updates
- To refresh internal state

### Exit

Completely stops the application.

**Confirmation:** Requires confirmation click to prevent accidental exit.

---

## Dark/Light Mode

Toggle between themes using the "Light/Dark" button in the header.

**Dark Mode (Default):**
- Dark blue gradient background
- Cyan accent colors
- Optimized for extended use

**Light Mode:**
- Light gray gradient background
- Teal accent colors
- Better for bright environments

**Persistence:** Theme preference is saved to browser localStorage.

---

## Result Verification

The app includes false positive detection to improve result reliability.

### How It Works

After finding a potential match:
1. **Pattern Check**: Scans response for negative indicators
2. **Content Verification**: Looks for username in page content
3. **URL Confirmation**: Verifies username in the URL

### Negative Patterns Detected

Results are marked "Unverified" if the response contains:
- "not found" / "doesn't exist"
- "user not found" / "profile not found"
- Generic login/signup pages
- 404 error pages
- Content requiring authentication

### Result Sorting

Results are automatically sorted:
1. **Verified** (first) - Highest confidence
2. **Unconfirmed** - Medium confidence
3. **Unverified** (last) - Likely false positives

---

## Export Features

### PDF Export

Export search results to a formatted PDF document.

**How to use:**
1. Complete a search
2. Look for the "Export to PDF" button
3. Click to download

**PDF Contents:**
- Search summary (tool, query, date)
- Result statistics
- Detailed findings list
- Profile URLs
- Verification status

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Start search |
| `Esc` | Clear input field |

---

## API Endpoints

The app provides REST API endpoints for programmatic access.

### Authentication

No authentication required (local use only).

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/social/stream` | Social media search (streaming) |
| POST | `/api/person/stream` | People search (streaming) |
| POST | `/api/email/combined` | Combined email search |
| POST | `/api/email/holehe` | Holehe only email search |
| POST | `/api/username/stream` | Username search (Sherlock) |
| POST | `/api/username/maigret` | Maigret username search |
| POST | `/api/phone-lookup` | Phone number lookup |
| POST | `/api/ip` | IP address lookup |
| POST | `/api/domain` | Domain lookup |
| GET | `/api/health` | Platform health status |
| GET | `/api/history` | Search history |
| GET | `/api/version` | Version information |
| POST | `/api/system/restart` | Restart server |
| POST | `/api/system/exit` | Exit server |

### Example API Call

```bash
curl -X POST http://localhost:5000/api/username/stream \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser"}'
```

---

## Troubleshooting

### Common Issues

**"Could not load Sherlock site data"**
- Ensure sherlock-project is installed: `pip install sherlock-project`
- Run once with internet to cache site data

**"Could not load Maigret database"**
- Ensure maigret is installed: `pip install maigret`
- Database downloads automatically on first run

**Slow Search Performance**
- Check internet connection
- Some platforms may rate-limit
- Use Health dashboard to check platform status
- Try stopping search early if you have enough results

**False Positives**
- Look for "Unverified" badge on results
- Check HTTP status code (200 vs other)
- Verify by visiting the profile URL
- Use multiple tools for cross-reference

**Phone Lookup Not Working**
- Ensure number is in international format
- WhatsApp/Telegram may limit profile access
- Check if phone number is registered on platforms

### Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| Network timeout | Platform not responding | Try again later or use Health check |
| Rate limited | Too many requests | Wait and retry with fewer sites |
| Invalid input | Wrong format | Check input format requirements |
| Service unavailable | Platform down | Check Health dashboard |

---

## Changelog

### Version 1.2.0 (Current)
- AI Assistant with Ollama integration
- Natural language search
- AI-powered result summaries
- Profile enrichment (basic)
- Purple AI Summary button on results

### Version 1.1.0
- Dark/Light mode toggle with theme persistence
- False positive filtering with verification levels
- Phone lookup (WhatsApp, Telegram, Carrier)
- Combined email search (Sherlock + Holehe)
- Platform health dashboard
- System controls (Restart, Exit)
- Improved UI with smaller tool cards
- Separated Sherlock and Maigret for better performance

### Version 1.0.0
- Initial Release
- Core OSINT tools (Email, Username, Social Media, People Search)
- IP and Domain lookup
- Search history and archiving
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
| ReportLab | PDF generation |
| httpx | Async HTTP requests |

### Caching

- Search results cached for 24 hours
- Cache key: `{search_type}:{query}:{category}`
- Reduces duplicate searches
- Improves response time

### Rate Limiting

- Platforms have individual rate limits
- Too many requests may result in temporary blocks
- Use Health dashboard to monitor status

---

## Support & Contributing

### Reporting Issues

To report bugs or request features:
1. Check existing issues
2. Provide detailed description
3. Include error messages
4. Specify steps to reproduce

### Security Considerations

- Use responsibly and legally
- Respect privacy and platform terms of service
- Do not use for harassment or illegal activities
- Results may contain false positives

---

## Appendix

### Platform Lists

**Social Media Platforms (56 sites):**
Facebook, Twitter/X, Instagram, LinkedIn, TikTok, Snapchat, Pinterest, YouTube, Reddit, Quora, Tumblr, Flickr, Vimeo, Medium, WordPress, Blogger, Twitch, Discord, Steam, GitHub, GitLab, Bitbucket, Stack Overflow, DeviantArt, Dribbble, Behance, SoundCloud, Bandcamp, Spotify, Last.fm, MySpace, Vine, Periscope, Clubhouse, Parler, Gab, Truth Social, Minds, Steemit, Hive, Mastodon, Lemmy, Pixelfed, Friendica, GNU Social, diaspora*, VK, OK, Mail.ru, Weibo, QQ, WeChat, Douyin, Bilibili, Roblox, Minecraft, Fortnite, Pokémon GO

**Developer Platforms:**
GitHub, GitLab, Bitbucket, Stack Overflow, Hacker News, dev.to, CodePen, Replit, Glitch

**Gaming Platforms:**
Steam, Discord, Twitch, Xbox Live

---

*This manual was generated for OSINT Dashboard v1.1.0*
