# Findings in official reports — policy decision

**Status:** owner decision implemented locally  
**Checked:** 2026-09-26

## Current behavior

Official HTML/PDF/template reports include a finding only when:

- it is not deleted;
- it is not archived; and
- its lifecycle status is `verified`; and
- `include_in_report` is `NULL` or `TRUE`.

Candidate, rejected and superseded findings are excluded from official reports.
The explicit report flag can still exclude a verified finding. Existing
findings without a verified lifecycle status require review before they can
appear in an official report.

Raw exports and operational finding lists intentionally use different rules and
are not automatically equivalent to an official report.

## Safeguards for the decided policy

- Display verification status prominently in report previews and generated
  reports.
- Record who changed `include_in_report` and when.
- Add a report summary count for candidate/unverified findings.
- Keep raw exports clearly labeled as working data, not official reports.
- Add regression tests for candidate, verified, rejected, archived and
  explicitly excluded findings in HTML, PDF and template reports.

## Implementation note

Raw exports and operational finding lists remain working-data views and do not
automatically apply the official-report filter. They must not be presented as
official reports.
