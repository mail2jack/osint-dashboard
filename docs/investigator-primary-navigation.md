# Investigator primary navigation rollout

`investigator_primary_navigation` is a tenant-scoped rollout flag, OFF by
default. It changes navigation only; it never authorizes a route or changes
case data. The new navigation takes effect only when the tenant's existing
`investigation_workspace` flag is also ON. Otherwise the existing navigation
is shown.

When effective, investigators see the workflow case list as the primary
**Cases** entry, and the separate **Legacy cases** menu item is hidden.
Readers without workflow case-list access see the existing authorized case
list under **Cases** instead. Existing `/cms/cases` and `/cms/cases/<id>`
bookmarks remain reachable for authorized users. Investigation breadcrumbs
and back-links follow the same role split.

Rollout: enable `investigation_workspace`, then enable
`investigator_primary_navigation` for one QA tenant through the super-admin
feature-flag UI. Test an investigator and a viewer/junior user, including
case access, investigation detail, report navigation, NL/EN and a narrow
viewport. Roll back by switching off `investigator_primary_navigation`; no
code or database rollback is needed. Do not remove legacy routes until their
remaining capabilities and incoming links have been inventoried and replaced.
