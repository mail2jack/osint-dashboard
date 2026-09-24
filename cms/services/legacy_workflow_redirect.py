"""Opt-in, reversible redirects from legacy case pages to the workflow UI."""

from flask import redirect, request, url_for
from flask_login import current_user

from cms.tier_limits import check_feature


_WORKFLOW_WRITER_ROLES = {"investigator", "senior_investigator", "admin", "owner"}


def redirect_legacy_case_get(target_endpoint: str, **values):
    """Return a temporary workflow redirect only for a fully rolled-out writer.

    This deliberately applies only to GET presentation pages.  Legacy POST,
    PDF, export, upload and compatibility endpoints retain their current
    behaviour, so disabling the tenant flag immediately restores the old UI.
    """
    if request.method != "GET" or current_user.role not in _WORKFLOW_WRITER_ROLES:
        return None
    tenant_id = current_user.tenant_id
    if not tenant_id or not all(
        check_feature(flag, tenant_id)
        for flag in (
            "investigator_primary_navigation",
            "investigation_workspace",
            "workflow_legacy_case_redirect",
        )
    ):
        return None
    return redirect(url_for(target_endpoint, **values), code=302)
