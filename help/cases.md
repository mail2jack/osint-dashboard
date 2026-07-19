# Cases

A **Case** is the central organizational unit of the system. It groups subjects, findings, documents, financial data, and comments into one investigation.

## Case Fields

| Field | Description |
|-------|-------------|
| **Title** | Short descriptive name of the investigation |
| **Status** | Open / In Progress / On Hold / Closed / Archived |
| **Priority** | Low / Medium / High / Critical |
| **Client** | The client (required) |
| **Description** | Extended description of the investigation |
| **Tags** | Labels for categorization |

## Status Transitions

The possible status transitions:

- **Open** → **In Progress** (investigation started)
- **In Progress** → **On Hold** (waiting for information)
- **In Progress** → **Closed** (completed)
- **On Hold** → **In Progress** (investigation resumed)
- **Closed** → **Archived** (archived)

Use the **State** section on the case detail page to change the status.

## Creating a Case

1. Click **Cases** in the header, then **New Case**
2. Fill in the required fields (Title, Client)
3. Select Status and Priority
4. Click **Save**

The case is created and you are redirected to the detail page.

## Case Detail Page

The detail page shows various sections:

### Subjects
Linked persons, companies, or vessels. Click **Link Subjects** to link existing subjects or create new ones. A single subject can be linked to multiple cases.

### Findings
OSINT findings organized by subject. Each finding has a type, confidence score, and source. Findings can be added via manual entry, SpiderFoot import, or OSINT Search.

### Documents
Uploaded files and screenshots (max 16 MB per file). Supported formats: PDF, images, Office documents.

### Financial Data
Bank transactions, invoices, and payment records. Each financial record has a type (credit/debit), amount, date, and counterparty.

### Comments
Internal team notes. Comments can be edited and have an edit history.

### Audit Log
A complete log of who did what and when.

## Reports

The **Reports** tab offers:

- **Case Summary** — overview generated from case data
- **CSV Export** — export findings to CSV
- **PDF Export** — export case report to PDF (if configured)

## Searching Cases

Use the search bar above the cases list to filter by title, status, client, or tags.

## Pagination

Findings, documents, and financials are displayed in pages of 20 items. Use the **Previous** / **Next** buttons to navigate.
