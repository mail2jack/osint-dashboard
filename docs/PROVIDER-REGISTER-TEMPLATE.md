# Joost — external provider register

Use one row per provider and per materially different data flow. An entry in
this template is not approval to activate the provider.

| Field | Value |
|---|---|
| Provider name |  |
| Joost feature/action |  |
| Tenant scope | Per tenant / installation / local only |
| Provider purpose |  |
| Data category | Public technical / business identifier / sensitive personal data |
| Exact input fields |  |
| Example input (synthetic only) |  |
| Output stored in Joost |  |
| Source/provenance retained | Yes / No / Description |
| Provider region |  |
| Provider retention |  |
| Joost retention |  |
| Approved for this category | Yes / No / Pending |
| Consent or lawful basis |  |
| DPA/terms reviewed | Yes / No / Date |
| Credential owner |  |
| Secret rotation procedure |  |
| Audit event |  |
| Deletion/disable procedure |  |
| Reviewer and date |  |

## Default Joost rules

- Public technical data may use an approved provider.
- Business identifiers require configured provider approval.
- Sensitive personal data requires explicit provider approval.
- Never use production personal data as a test input.
- Do not activate a provider solely because credentials are present.
- Keep provider results and retention behavior tenant-scoped where the feature
  is tenant-scoped.
