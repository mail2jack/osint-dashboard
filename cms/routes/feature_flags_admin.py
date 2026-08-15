import logging

from flask import abort, request, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Tenant, FeatureFlag

logger = logging.getLogger(__name__)

FEATURE_FLAG_NAMES = {
    "export": "📤 Export (CSV/PDF)",
    "ai": "🤖 AI Summarization",
    "spiderfoot": "🕷️ SpiderFoot OSINT",
    "api_keys": "🔑 API Key Access",
    "paid_channels": "💰 Paid Channels",
    "subject_first_investigations": "👤 Subject-First Investigations",
}

FEATURE_FLAG_ORDER = [
    "export",
    "ai",
    "spiderfoot",
    "api_keys",
    "paid_channels",
    "subject_first_investigations",
]

# Flags that are off by default for every tier (ADR-0001 D1.6 / D1.7).
_OFF_BY_DEFAULT = {"paid_channels", "subject_first_investigations"}


def _flag_tier_default(flag_name: str, tenant) -> bool:
    """The tier default a flag resolves to without a super-admin override.

    ``paid_channels`` and ``subject_first_investigations`` are off by default
    for every tier (ADR-0001 D1.6/D1.7); the tier flags use their plan default.
    """
    if flag_name in _OFF_BY_DEFAULT:
        return False
    tier_default = tenant.tier in ("professional", "enterprise")
    if flag_name == "export":
        tier_default = tenant.tier in ("starter", "professional", "enterprise")
    return tier_default


@cms_bp.route("/admin/feature-flags")
@login_required
def admin_feature_flags():
    """Super-admin: view and toggle per-tenant feature flag overrides."""
    if not current_user.is_super_admin:
        abort(403)

    tenants = Tenant.query.order_by(Tenant.name).all()

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
            flags[name] = override
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

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        flash("Tenant not found.", "error")
        return redirect(url_for("cms.admin_feature_flags"))

    override = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name=flag_name
    ).first()

    tier_default = _flag_tier_default(flag_name, tenant)

    if enabled == tier_default:
        # Matches tier default — remove the override
        if override:
            db.session.delete(override)
            flash(f"Removed override for {flag_name} — using tier default.", "info")
    else:
        if override:
            override.enabled = enabled
        else:
            override = FeatureFlag(
                tenant_id=tenant_id, flag_name=flag_name, enabled=enabled
            )
            db.session.add(override)
        flash(
            f"{'Enabled' if enabled else 'Disabled'} {flag_name} for {tenant.name}.",
            "success",
        )

    db.session.commit()
    return redirect(url_for("cms.admin_feature_flags"))
