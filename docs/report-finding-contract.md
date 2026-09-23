# Findings in official reports

The workflow PV, case HTML/PDF reports, and generated template reports use
`report_visible_finding_filter()`. A finding is eligible when it belongs to
the requested case, is neither soft-deleted nor archived, and its
`include_in_report` value is `NULL` or `True`. An explicit `False` excludes
it. Verification status is independent of inclusion; a report can therefore
include an unverified finding. The UI should show these as separate states.

An included finding carries its title, content and investigator comment.
HTML/PV/PDF render eligible local screenshot evidence; the generated template
context exposes the same screenshot metadata to templates. A captured source
URL is a link only when it is HTTP(S). External URLs are never embedded as
images. Raw exports and operational finding lists are deliberately not subject
to this official-report filter.

For rollout, compare the same case in PV, HTML, PDF and a template-generated
report with included, excluded, archived and deleted findings. Confirm
comment and screenshot behavior without changing real case data.
