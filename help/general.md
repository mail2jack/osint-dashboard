# General

The OSINT Dashboard is a web application for managing investigations (cases), recording findings, and performing OSINT searches.

## Navigation

The main navigation bar contains:

- **Dashboard** — central hub with statistics and recent activity
- **Cases** — overview of all investigations
- **Clients** — clients linked to cases
- **Subjects** — persons, companies, or vessels of interest
- **Search** — full-text search across the entire database
- **Reminders** — reminders and notifications
- **SpiderFoot** — OSINT scan automation (senior/admin only)
- **Settings** — application configuration (admin only)

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `?` | Open context-sensitive help panel |
| `s` | Focus the search bar (on search pages) |
| `j` / `k` | Navigate down/up in lists |
| `Enter` | Open selected item |
| `Escape` | Close modals / help panel |

## Theme

Click the 🌙/☀️ icon in the top right to toggle between dark/light mode. The choice is saved in localStorage.

## Session

The session expires after 8 hours of inactivity. Attached files are limited to 16 MB per upload.

## Rate Limiting

To prevent abuse, there is a global limit of 300 requests per 60 seconds per IP. For create/edit actions, a stricter limit of 30 requests per 60 seconds applies.
