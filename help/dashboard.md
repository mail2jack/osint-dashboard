# Dashboard

The Dashboard is the home page after logging in. It is now **search-centric**: the search bar is prominently centered for quick searching across cases, subjects, clients, findings, and documents.

## Search

Type a search term in the central bar and press Enter — you are redirected to the search page with results. Or use the quick links below to navigate directly to a section.

## Quick Links

- 📁 All Cases
- ➕ New Case
- 👤 All Subjects
- ➕ New Subject
- 🏢 All Clients
- ⏰ Reminders
- 📥 Export
- ⚙️ Settings (admin only)
- 📊 Statistics (admin only)

## Stat Counters

A row of 7 cards shows the total count of:

- **Open** — cases with status Open
- **Active** — cases with status Active
- **Suspended** — cases with status Suspended
- **Closed** — cases with status Closed
- **Clients** — active clients
- **Subjects** — total subjects
- **Findings** — total findings

## My Open / Active Cases

Table with your assigned cases (status Open or Active), sorted by last update.

## OSINT Service Health

A row of 7 service cards shows the status of external OSINT sources:

- **Database** — PostgreSQL connection
- **SpiderFoot** — OSINT scan engine
- **RDW** — Dutch vehicle registration
- **Kadaster/PDOK** — Dutch BAG address data
- **HIBP** — Have I Been Pwned (data breach check)
- **Overheid.io** — Dutch open data API (OpenKVK KvK lookup)
- **Brave Search** — web search API

Green = Online, Orange = no key configured, Red = error. Health is checked when the page loads.

## Statistics

For all charts and widgets (Cases by Status, Criminal Code, Priority, Lead Investigator Workload, Recent Activity, SpiderFoot stats, Reminders, Subject Types) go to **Settings → Statistics** (`/cms/settings/statistics`). This page contains all former dashboard widgets.
