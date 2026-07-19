# Settings

The **Settings** page manages the application configuration. Only admin users have access.

## Statistics

The **📊 Statistics** page (`/cms/settings/statistics`) contains all former dashboard widgets:
- Cases by Status, Priority and Criminal Code (bar charts)
- Lead Investigator Workload
- My Active Cases & Priority Cases
- Reminders (overdue/upcoming)
- Subject Types
- SpiderFoot Scans overview
- Recent Activity feed

This page is accessible via the Settings sidebar or via the quick links on the dashboard.

## General Settings

| Setting | Description |
|---------|-------------|
| **Application Name** | Customize the dashboard title |
| **Theme Style** | Classic or professional theme |

## API Keys

Store API keys for external services. These are stored in the database (not in `.env`).

### OSINT Services

| Setting | Service | Obtain via |
|---------|---------|------------|
| `spiderfoot_url` | SpiderFoot URL | Self-hosted instance |
| `spiderfoot_username` | SpiderFoot username | `~/.spiderfoot/passwd` |
| `spiderfoot_password` | SpiderFoot password | `~/.spiderfoot/passwd` |
| `overheid_api_key` | Overheid.io API | overheid.io |
| `brave_api_key` | Brave Search API | brave.com/search/api |
| `twoc` | TwoChat WhatsApp | twochat.nl |

### Vessel Lookups

| Setting | Service | Obtain via |
|---------|---------|------------|
| `marineplan_api_key` | MarinePlan | marineplan.com |
| `equasis_email` | Equasis login | equasis.org (free registration) |
| `equasis_password` | Equasis password | equasis.org |

### WhatsApp Presence

| Setting | Service | Obtain via |
|---------|---------|------------|
| `whatsapp_checkleaked_key` | whatsapp.checkleaked.cc | RapidAPI (50 req/month free) |

## Update Settings

- **Update Check Repo** — GitHub repository to check for updates (format: `owner/repo`)
- The dashboard checks on every page load whether new versions or commits are available
- When an update is available, a blue banner appears at the top of the page
- Click **Update Now** to perform the update (requires sudo privileges for git/chown/systemctl)

## Encryption

Sensitive fields (ID numbers, license plates, IMO/MMSI/ENI) are stored encrypted with **Fernet** encryption.

The encryption key is:
1. Read from the `CMS_ENCRYPTION_KEY` environment variable
2. Or from the `.cms_key` file in the project root
3. Or automatically generated on first start

## User Management

From Settings, admins can:

- View all users
- Create new users (sends a "Set Password" email)
- Change user roles:
  - **Admin** — full access
  - **Senior Investigator** — all features except settings
  - **Investigator** — standard investigator
  - **Viewer** — read only
- Disable or delete users
- Send password reset

## Password Reset Flow

1. Admin creates a new user or clicks "Reset Password"
2. System generates a token (valid for 48 hours)
3. User receives email with reset link
4. User chooses a new password (minimum 8 characters)
5. Token is used once and immediately deleted

Note: passwords are **never** sent via email.
