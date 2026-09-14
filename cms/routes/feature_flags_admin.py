import logging

from flask import abort, flash, redirect, render_template, request, session, url_for
from flask_login import login_required, current_user

from . import cms_bp
from ..feature_flag_policy import (
    FEATURE_FLAG_NAMES,
    FEATURE_FLAG_ORDER,
    OFF_BY_DEFAULT,
    tier_default,
)
from ..models import FeatureFlag, Tenant, db
from ..services.feature_flag_service import set_feature_flag

logger = logging.getLogger(__name__)

# Backwards-compatible import for existing callers/tests; policy lives centrally.
_OFF_BY_DEFAULT = OFF_BY_DEFAULT

def _flag_tier_default(flag_name: str, tenant) -> bool:
    """Compatibility wrapper around the canonical pure policy."""
    return tier_default(flag_name, tenant.tier)


def _manageable_tenant_query():
    switched = session.get("switched_tenant_id")
    query = Tenant.query
    return query.filter(Tenant.id == switched) if switched else query


@cms_bp.route("/admin/feature-flags")
@login_required
def admin_feature_flags():
    """Super-admin: view and toggle per-tenant feature flag overrides."""
    if not current_user.is_super_admin:
        abort(403)

    tenants = _manageable_tenant_query().order_by(Tenant.name).all()

    # Build a lookup: (tenant_id, flag_name) -> FeatureFlag
    all_flags = FeatureFlag.query.all()
    flag_map = {}
    for f in all_flags:
        flag_map[(f.tenant_id, f.flag_name)] = f

    rows = []
    for t in tenants:
        flags = {}
        for name in FEATURE_FLAG_ORDER:
            override = flag_map.get((t.id, name))
            flags[name] = {
                "override": override,
                "tier_default": tier_default(name, t.tier),
            }
        rows.append({"tenant": t, "flags": flags})

    return render_template(
        "cms/feature_flags.html",
        rows=rows,
        flag_names=FEATURE_FLAG_NAMES,
        flag_order=FEATURE_FLAG_ORDER,
    )


@cms_bp.route("/admin/feature-flags/toggle", methods=["POST"])
@login_required
def admin_feature_flag_toggle():
    """Super-admin: toggle a single feature flag for a tenant."""
    if not current_user.is_super_admin:
        abort(403)

    tenant_id = request.form.get("tenant_id")
    flag_name = request.form.get("flag_name")
    enabled = request.form.get("enabled") == "1"

    if not tenant_id or not flag_name:
        flash("Missing tenant_id or flag_name.", "error")
        return redirect(url_for("cms.admin_feature_flags"))

    if flag_name not in FEATURE_FLAG_NAMES:
        flash(f"Unknown flag: {flag_name}", "error")
        return redirect(url_for("cms.admin_feature_flags"))

    tenant = _manageable_tenant_query().filter(Tenant.id == tenant_id).first()
    if not tenant:
        flash("Tenant not found.", "error")
        return redirect(url_for("cms.admin_feature_flags"))

    try:
        change = set_feature_flag(
            tenant=tenant,
            flag_name=flag_name,
            enabled=enabled,
            actor_id=current_user.id,
            source="super_admin_ui",
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to change feature flag")
        flash("Could not update feature flag.", "error")
        return redirect(url_for("cms.admin_feature_flags"))

    if change.operation == "delete":
        flash(f"Removed override for {flag_name} — using tier default.", "info")
    elif change.changed:
        flash(
            f"{'Enabled' if enabled else 'Disabled'} {flag_name} for {tenant.name}.",
            "success",
        )
    return redirect(url_for("cms.admin_feature_flags"))
