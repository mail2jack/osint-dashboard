import json
import logging

from flask import abort, request, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Tenant, Setting
from ..rate_limiting import (
    get_tier_rate_limit,
    get_tenant_api_rate_status,
    _SETTING_KEY_TIER,
    _SETTING_KEY_OVERRIDES,
)

logger = logging.getLogger(__name__)


def _get_tier_defaults() -> dict:
    raw = Setting.get(_SETTING_KEY_TIER)
    if raw:
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _get_overrides() -> dict:
    raw = Setting.get(_SETTING_KEY_OVERRIDES)
    if raw:
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


@cms_bp.route("/admin/rate-limits")
@login_required
def admin_rate_limits():
    """Super-admin: view and configure per-tenant rate limits."""
    if not current_user.is_super_admin:
        abort(403)

    tenants = Tenant.query.order_by(Tenant.name).all()
    tier_defaults = _get_tier_defaults()
    overrides = _get_overrides()
    tenant_status = get_tenant_api_rate_status()
    status_map = {s["tenant_key"]: s for s in tenant_status}

    rows = []
    for t in tenants:
        tier_limit = get_tier_rate_limit(t.tier)
        override = overrides.get(t.id)
        effective = override if override is not None else tier_limit
        key = f"tenant:{t.id}"
        usage = status_map.get(key, {})

        rows.append(
            {
                "tenant": t,
                "tier_limit": tier_limit,
                "override": override,
                "effective": effective,
                "current_requests": usage.get("count", 0),
                "remaining": max(0, effective - usage.get("count", 0)),
            }
        )

    return render_template(
        "cms/rate_limits_admin.html",
        rows=rows,
        tier_defaults=tier_defaults,
    )


@cms_bp.route("/admin/rate-limits/update-override", methods=["POST"])
@login_required
def admin_rate_limits_update_override():
    """Super-admin: set or clear a per-tenant rate limit override."""
    if not current_user.is_super_admin:
        abort(403)

    tenant_id = request.form.get("tenant_id")
    requests_per_min = request.form.get("requests_per_min", "").strip()

    if not tenant_id:
        flash("Missing tenant_id.", "error")
        return redirect(url_for("cms.admin_rate_limits"))

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        flash("Tenant not found.", "error")
        return redirect(url_for("cms.admin_rate_limits"))

    overrides = _get_overrides()

    if requests_per_min == "" or int(requests_per_min) <= 0:
        # Clear override
        overrides.pop(tenant_id, None)
        flash(
            f"Cleared rate limit override for {tenant.name} — using tier default.",
            "info",
        )
    else:
        val = int(requests_per_min)
        overrides[tenant_id] = val
        flash(
            f"Set rate limit override for {tenant.name} to {val} requests/min.",
            "success",
        )

    Setting.set(_SETTING_KEY_OVERRIDES, json.dumps(overrides), category="general")
    db.session.commit()
    return redirect(url_for("cms.admin_rate_limits"))


@cms_bp.route("/admin/rate-limits/update-tier-default", methods=["POST"])
@login_required
def admin_rate_limits_update_tier_default():
    """Super-admin: update a tier's default rate limit."""
    if not current_user.is_super_admin:
        abort(403)

    tier = request.form.get("tier")
    limit_str = request.form.get("limit", "").strip()

    if not tier or not limit_str:
        flash("Missing tier or limit value.", "error")
        return redirect(url_for("cms.admin_rate_limits"))

    valid_tiers = {"free", "starter", "professional", "enterprise"}
    if tier not in valid_tiers:
        flash(f"Unknown tier: {tier}", "error")
        return redirect(url_for("cms.admin_rate_limits"))

    try:
        val = int(limit_str)
        if val < 1:
            raise ValueError
    except ValueError:
        flash("Limit must be a positive integer.", "error")
        return redirect(url_for("cms.admin_rate_limits"))

    tier_defaults = _get_tier_defaults()
    tier_defaults[tier] = val
    Setting.set(_SETTING_KEY_TIER, json.dumps(tier_defaults), category="general")
    db.session.commit()

    flash(f"Updated {tier} tier default rate limit to {val} requests/min.", "success")
    return redirect(url_for("cms.admin_rate_limits"))
