# Exports

The **Export** system allows you to export data from the dashboard for further processing or reporting.

## CSV Export

Findings can be exported to CSV format. This is available from:

- The **case detail page** (Reports tab → Export CSV)
- The **search results page**

### CSV Fields

The exported CSV file contains:

- Type (email, IP, domain, etc.)
- Data (the found value)
- Source (which module/service)
- Confidence score (0-100)
- Date
- Case information
- Subject information

### Limitations

- A warning appears when there are more than 5,000 records
- Export uses `yield_per(200)` to limit memory usage
- Only users with export permissions can export

## Case Reports

The **Reports** tab on the case detail page offers:

- **Case Summary** — automatically generated overview of the case with all linked subjects, findings, and documents
- **PDF Export** — if configured, export the full case report as PDF

## Audit Log Export

Audit logs can be viewed and filtered on the **Audit** page but are not directly exported (refer to the database for bulk exports).
