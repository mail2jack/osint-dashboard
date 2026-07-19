# Audit Log

The **Audit Log** system records all important actions in the dashboard. It provides a fully traceable overview of who did what and when.

## What is logged?

| Action | Example |
|--------|---------|
| **Case create/edit** | New case, status change, priority change |
| **Subject create/edit** | New subject, data change |
| **Client create/edit** | New client, address change |
| **Finding add/edit** | New OSINT finding |
| **Document upload** | File added to case |
| **Comment** | Comment posted or edited |
| **Login** | User login/logout |
| **Export** | Data exported |
| **SpiderFoot** | Scan started or results linked |
| **Settings change** | API keys or configuration updated |

## Viewing the Audit Log

1. Go to **Cases** → open a case
2. Scroll to the **Audit Log** section at the bottom of the page
3. Or use the dedicated audit page for a global overview

Each entry shows:

- **Timestamp** — date and time
- **User** — which user
- **Action** — what happened
- **Details** — additional information (e.g. "Status changed from Open to In Progress")
- **IP Address** — where the action was performed from

## Cleanup

Audit logs are automatically cleaned up according to the retention period configured in settings. Old logs are periodically removed to manage database size.
