import logging
from datetime import datetime, timezone, timedelta

import flask
from flask import (
    request,
    jsonify,
    render_template,
    abort,
    flash,
    redirect,
    url_for,
    current_app,
)
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db,
    Setting,
    AuditLog,
    LoginLog,
    User,
    Tenant,
    PlatformSetting,
    TenantSetting,
    init_default_settings,
)
from ..auth import admin_required
from ..validation import validate, SaveSettingsSchema

from .response import api_success, api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/settings")
@login_required
def settings() -> str:
    """Settings management page."""
    category = request.args.get("category", "api_keys")
    search_q = request.args.get("q", "").strip()

    categories = {
        "api_keys": {"name": "🔑 API Keys", "icon": "🔑", "group": "integrations"},
        "search": {"name": "🔍 Search", "icon": "🔍", "group": "integrations"},
        "email": {"name": "📧 Email", "icon": "📧", "group": "integrations"},
        "telegram": {"name": "📱 Telegram Bot", "icon": "📱", "group": "integrations"},
        "spiderfoot": {"name": "🕷️ SpiderFoot", "icon": "🕷️", "group": "integrations"},
        "ai": {"name": "🤖 AI Provider", "icon": "🤖", "group": "integrations"},
        "general": {"name": "⚙️ General", "icon": "⚙️", "group": "system"},
        "security": {"name": "🔒 Security", "icon": "🔒", "group": "system"},
        "appearance": {"name": "🎨 Appearance", "icon": "🎨", "group": "system"},
        "feature_flags": {"name": "🚩 Feature Flags", "icon": "🚩", "group": "system"},
        "plan": {"name": "📋 Plan & Limits", "icon": "📋", "group": "system"},
    }

    is_platform_cat = False
    plan_info = None
    settings_list = []
    if category == "plan":
        if not current_user.is_admin:
            abort(403)
        from ..tier_limits import TIERS, TIER_DISPLAY

        tier = current_user.tenant.tier if current_user.tenant else "free"
        limits = TIERS.get(tier, TIERS["free"])
        from ..models import User, Case

        user_count = User.query.filter_by(tenant_id=current_user.tenant_id).count()
        case_count = Case.query.filter_by(
            tenant_id=current_user.tenant_id, is_deleted=False
        ).count()
        tenant = current_user.tenant
        plan_info = {
            "tier": tier,
            "tier_display": TIER_DISPLAY.get(tier, tier.title()),
            "limits": limits,
            "user_count": user_count,
            "case_count": case_count,
            "subscription_status": tenant.subscription_status if tenant else None,
            "stripe_customer_id": tenant.stripe_customer_id if tenant else None,
            "stripe_configured": bool(current_app.config.get("STRIPE_SECRET_KEY")),
        }
    elif category == "platform":
        if not current_user.is_super_admin:
            abort(403)
        is_platform_cat = True
        settings_list = PlatformSetting.query.order_by(PlatformSetting.key).all()
    else:
        if not current_user.is_admin:
            abort(403)
        settings_list = (
            Setting.query.filter_by(category=category, is_active=True)
            .order_by(Setting.display_order)
            .all()
        )

    return render_template(
        "cms/settings/index.html",
        settings_list=settings_list,
        categories=categories,
        active_category=category,
        search_query=search_q,
        is_platform_cat=is_platform_cat,
        plan_info=plan_info,
    )


@cms_bp.route("/api/settings")
@login_required
@admin_required
def get_settings_api() -> flask.Response:
    """Get all settings grouped by category (masked values)."""
    categories = request.args.get("category", None)

    query = Setting.query.filter_by(is_active=True)
    if categories:
        query = query.filter_by(category=categories)

    settings_list = query.order_by(Setting.category, Setting.display_order).all()

    return jsonify(
        {"settings": [s.to_dict(include_value=False) for s in settings_list]}
    )


@cms_bp.route("/api/settings/search")
@login_required
@admin_required
def search_settings_api() -> flask.Response:
    """Search settings across all categories by key, description, or category."""
    query = request.args.get("q", "").strip()

    if not query or len(query) < 2:
        return jsonify({"settings": [], "total": 0})

    settings = (
        Setting.query.filter(
            Setting.is_active == True,
            db.or_(
                Setting.key.ilike(f"%{query}%"),
                Setting.description.ilike(f"%{query}%"),
                Setting.value.ilike(f"%{query}%"),
                Setting.category.ilike(f"%{query}%"),
            ),
        )
        .order_by(Setting.category, Setting.display_order)
        .all()
    )

    grouped = {}
    for s in settings:
        cat = s.category or "general"
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(s.to_dict(include_value=False))

    return jsonify(
        {
            "query": query,
            "total": len(settings),
            "grouped": grouped,
            "categories": list(grouped.keys()),
        }
    )


@cms_bp.route("/api/settings/<setting_id>", methods=["GET"])
@login_required
@admin_required
def get_setting_api(setting_id: str) -> flask.Response:
    """Get a single setting."""
    setting = db.session.get(Setting, setting_id) or abort(404)
    return jsonify(setting.to_dict(include_value=not setting.is_sensitive))


@cms_bp.route("/api/settings", methods=["POST"])
@login_required
@admin_required
@validate(SaveSettingsSchema)
def save_settings_api() -> flask.Response:
    """Save one or more settings."""
    saved_count = 0
    errors = []

    for item in request.validated_data.get("settings", []):
        setting_id = item.get("id")
        new_value = item.get("value")

        if setting_id:
            setting = db.session.get(Setting, setting_id)
            if setting:
                old_value = (
                    setting.get_masked_value()
                    if setting.is_sensitive
                    else setting.value
                )
                setting.value = new_value
                setting.updated_at = datetime.now(timezone.utc)

                AuditLog.log(
                    user_id=current_user.id,
                    action="setting_updated",
                    entity_type="setting",
                    entity_id=setting_id,
                    changes={
                        "value": {
                            "old": old_value,
                            "new": "***MASKED***"
                            if setting.is_sensitive
                            else new_value,
                        }
                    },
                    ip_address=request.remote_addr,
                    description=f"Updated setting: {setting.key}",
                )
                saved_count += 1
        else:
            errors.append(f"Missing setting ID for: {item.get('key', 'unknown')}")

    db.session.commit()

    # Reinitialize default settings if needed
    try:
        init_default_settings()
    except Exception as e:
        logger.warning(
            f"Failed to initialize default settings ({type(e).__name__}): {e}"
        )

    return jsonify(
        {
            "message": f"Saved {saved_count} setting(s)",
            "saved": saved_count,
            "errors": errors,
        }
    )


# =============================================================================
# Platform Settings (super admin only)
# =============================================================================


@cms_bp.route("/api/platform-settings")
@login_required
@admin_required
def get_platform_settings() -> flask.Response:
    """Get all platform settings."""
    if not current_user.is_super_admin:
        abort(403)
    settings_list = PlatformSetting.query.order_by(PlatformSetting.key).all()
    return jsonify(
        {
            "settings": [
                {
                    "id": s.id,
                    "key": s.key,
                    "value": "***MASKED***" if s.is_encrypted else s.value,
                    "category": s.category,
                    "description": s.description,
                    "is_encrypted": s.is_encrypted,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in settings_list
            ]
        }
    )


@cms_bp.route("/api/platform-settings", methods=["POST"])
@login_required
@admin_required
def save_platform_settings() -> flask.Response:
    """Save platform settings."""
    if not current_user.is_super_admin:
        abort(403)
    data = request.get_json() or {}
    saved_count = 0
    for item in data.get("settings", []):
        setting_id = item.get("id")
        new_value = item.get("value")
        if setting_id:
            setting = db.session.get(PlatformSetting, setting_id)
            if setting:
                setting.value = new_value
                setting.updated_at = datetime.now(timezone.utc)
                saved_count += 1
    db.session.commit()
    return jsonify(
        {"message": f"Saved {saved_count} platform setting(s)", "saved": saved_count}
    )


# =============================================================================
# Tenant Settings (tenant owner only)
# =============================================================================


@cms_bp.route("/api/tenant-settings")
@login_required
def get_tenant_settings() -> flask.Response:
    """Get settings for the current tenant."""
    if not current_user.is_tenant_owner and not current_user.is_super_admin:
        abort(403)
    tid = current_user.tenant_id
    settings_list = (
        TenantSetting.query.filter_by(tenant_id=tid).order_by(TenantSetting.key).all()
    )
    return jsonify(
        {
            "settings": [
                {
                    "id": s.id,
                    "key": s.key,
                    "value": "***MASKED***" if s.is_encrypted else s.value,
                    "category": s.category,
                    "description": s.description,
                    "is_encrypted": s.is_encrypted,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in settings_list
            ]
        }
    )


@cms_bp.route("/api/tenant-settings", methods=["POST"])
@login_required
def save_tenant_settings() -> flask.Response:
    """Save settings for the current tenant."""
    if not current_user.is_tenant_owner and not current_user.is_super_admin:
        abort(403)
    data = request.get_json() or {}
    tid = current_user.tenant_id
    saved_count = 0
    for item in data.get("settings", []):
        setting_id = item.get("id")
        new_value = item.get("value")
        if setting_id:
            setting = db.session.get(TenantSetting, setting_id)
            if setting and setting.tenant_id == tid:
                setting.value = new_value
                setting.updated_at = datetime.now(timezone.utc)
                saved_count += 1
    db.session.commit()
    return jsonify(
        {"message": f"Saved {saved_count} tenant setting(s)", "saved": saved_count}
    )


# =============================================================================
# Platform Settings Reset
# =============================================================================


@cms_bp.route("/api/platform-settings/<setting_id>/reset", methods=["POST"])
@login_required
@admin_required
def reset_platform_setting(setting_id: str) -> flask.Response:
    """Delete a platform setting (reset to default)."""
    if not current_user.is_super_admin:
        abort(403)
    setting = db.session.get(PlatformSetting, setting_id) or abort(404)
    db.session.delete(setting)
    AuditLog.log(
        user_id=current_user.id,
        action="platform_setting_reset",
        entity_type="platform_setting",
        entity_id=setting_id,
        ip_address=request.remote_addr,
        description=f"Reset platform setting: {setting.key}",
    )
    db.session.commit()
    return api_success({}, "Platform setting reset to default")


@cms_bp.route("/api/settings/<setting_id>/reset", methods=["POST"])
@login_required
@admin_required
def reset_setting_api(setting_id: str) -> flask.Response:
    """Reset a setting to its default value."""
    setting = db.session.get(Setting, setting_id) or abort(404)

    # Remove the setting (will be recreated by init_default_settings)
    setting.is_active = False
    setting.updated_at = datetime.now(timezone.utc)

    AuditLog.log(
        user_id=current_user.id,
        action="setting_reset",
        entity_type="setting",
        entity_id=setting_id,
        ip_address=request.remote_addr,
        description=f"Reset setting: {setting.key}",
    )

    db.session.commit()

    # Reinitialize to get default value
    init_default_settings()

    return api_success({}, "Setting reset to default")


@cms_bp.route("/settings/read-audit")
@login_required
@admin_required
def read_audit_dashboard() -> str:
    """Browse audit log entries filtered by 'read' actions."""
    page = request.args.get("page", 1, type=int)
    per_page = 50
    entity_filter = request.args.get("type", "").strip()
    user_filter = request.args.get("user", "").strip()

    q = AuditLog.query.filter(AuditLog.action == "read")

    if entity_filter:
        q = q.filter(AuditLog.entity_type == entity_filter)
    if user_filter:
        q = q.join(AuditLog.user).filter(User.username.ilike(f"%{user_filter}%"))

    q = q.order_by(AuditLog.timestamp.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    entity_types = [
        r[0]
        for r in db.session.query(AuditLog.entity_type)
        .filter(AuditLog.action == "read")
        .distinct()
        .all()
    ]

    users = User.query.order_by(User.username).all()

    return render_template(
        "cms/settings/read_audit.html",
        logs=logs,
        pagination=pagination,
        entity_types=entity_types,
        users=users,
        entity_filter=entity_filter,
        user_filter=user_filter,
    )


@cms_bp.route("/settings/login-history")
@login_required
@admin_required
def login_history() -> str:
    """View login history with anomaly flags."""
    page = request.args.get("page", 1, type=int)
    per_page = 50
    show_anomalies_only = request.args.get("anomalies", "").lower() == "1"
    user_filter = request.args.get("user", "").strip()

    q = LoginLog.query
    if show_anomalies_only:
        q = q.filter(LoginLog.is_anomaly == True)
    if user_filter:
        q = q.join(LoginLog.user).filter(User.username.ilike(f"%{user_filter}%"))

    q = q.order_by(LoginLog.created_at.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    users = User.query.order_by(User.username).all()

    return render_template(
        "cms/settings/login_history.html",
        logs=logs,
        pagination=pagination,
        users=users,
        show_anomalies_only=show_anomalies_only,
        user_filter=user_filter,
    )


@cms_bp.route("/api/login-logs/<log_id>/dismiss-anomaly", methods=["POST"])
@login_required
@admin_required
def dismiss_anomaly(log_id: str) -> flask.Response:
    """Mark a login anomaly as reviewed/dismissed."""
    log = db.session.get(LoginLog, log_id) or abort(404)
    log.is_anomaly = False
    log.anomaly_reason = ""
    db.session.commit()
    return api_success({}, "Anomaly dismissed")


@cms_bp.route("/api/login-logs/purge", methods=["POST"])
@login_required
@admin_required
def purge_login_logs() -> flask.Response:
    """Delete login logs older than N days."""
    days = request.json.get("days", 90) if request.is_json else 90
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = LoginLog.query.filter(LoginLog.created_at < cutoff).delete()
    if deleted:
        db.session.commit()
    return api_success({}, f"Deleted {deleted} login log(s)")


@cms_bp.route("/settings/api-keys")
@login_required
@admin_required
def manage_api_keys() -> str:
    """View and manage per-user API keys."""
    from ..models import ApiKey

    keys = ApiKey.query.order_by(ApiKey.created_at.desc()).all()
    users = User.query.order_by(User.username).all()

    scope_options = ["read", "write", "admin"]

    return render_template(
        "cms/settings/api_keys.html",
        keys=keys,
        users=users,
        scope_options=scope_options,
    )


@cms_bp.route("/api/api-keys/create", methods=["POST"])
@login_required
@admin_required
def api_create_api_key() -> flask.Response:
    """Create a new API key for a user."""
    from ..models import ApiKey
    from ..tier_limits import check_feature

    if not check_feature("api_keys"):
        return api_error(
            "API keys are not available on your current plan. Upgrade to access this feature.",
            403,
        )
    data = request.get_json() or {}
    user_id = data.get("user_id")
    name = data.get("name", "").strip()
    raw_scopes = data.get("scopes", ["read"])

    if not user_id or not name:
        return api_error("user_id and name are required", 400)
    user = db.session.get(User, user_id)
    if not user:
        return api_error("User not found", 404)

    valid_scopes = {"read", "write", "admin"}
    scopes = [s for s in raw_scopes if s in valid_scopes]
    if not scopes:
        scopes = ["read"]

    raw_key, key_hash = ApiKey.generate_key()
    prefix = raw_key[:8]

    key_record = ApiKey(
        name=name,
        key_hash=key_hash,
        key_prefix=prefix,
        user_id=user_id,
        scopes=scopes,
        is_active=True,
    )
    db.session.add(key_record)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="api_key",
        entity_id=key_record.id,
        ip_address=request.remote_addr,
        description=f"Created API key '{name}' for user {user.username}",
    )
    db.session.commit()

    return jsonify(
        {
            "message": f"API key '{name}' created",
            "key": raw_key,
            "id": key_record.id,
        }
    )


@cms_bp.route("/api/api-keys/<key_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def api_deactivate_api_key(key_id: str) -> flask.Response:
    """Deactivate an API key."""
    from ..models import ApiKey

    key = db.session.get(ApiKey, key_id) or abort(404)
    key.is_active = False
    db.session.commit()
    return api_success({}, "API key deactivated")


@cms_bp.route("/api/api-keys/<key_id>/activate", methods=["POST"])
@login_required
@admin_required
def api_activate_api_key(key_id: str) -> flask.Response:
    """Reactivate an API key."""
    from ..models import ApiKey

    key = db.session.get(ApiKey, key_id) or abort(404)
    key.is_active = True
    db.session.commit()
    return api_success({}, "API key reactivated")


@cms_bp.route("/api/api-keys/<key_id>/delete", methods=["POST"])
@login_required
@admin_required
def api_delete_api_key(key_id: str) -> flask.Response:
    """Delete an API key."""
    from ..models import ApiKey

    key = db.session.get(ApiKey, key_id) or abort(404)
    db.session.delete(key)
    db.session.commit()
    return api_success({}, "API key deleted")


# =============================================================================
# Tenant Management (super admin only)
# =============================================================================


@cms_bp.route("/tenants")
@login_required
@admin_required
def list_tenants() -> str:
    """List all tenants (super admin only)."""
    if not current_user.is_super_admin:
        abort(403)
    tenants = Tenant.query.order_by(Tenant.created_at.desc()).all()
    from collections import Counter

    user_counts = Counter(
        r[0]
        for r in db.session.query(User.tenant_id)
        .filter(User.tenant_id.isnot(None))
        .all()
    )
    return render_template(
        "cms/settings/tenants.html", tenants=tenants, user_counts=user_counts
    )


@cms_bp.route("/api/tenants")
@login_required
@admin_required
def get_tenants_api() -> flask.Response:
    """Get all tenants with user counts."""
    if not current_user.is_super_admin:
        abort(403)
    tenants = Tenant.query.order_by(Tenant.created_at.desc()).all()
    return jsonify(
        {
            "tenants": [
                {
                    **t.to_dict(),
                    "user_count": User.query.filter_by(tenant_id=t.id).count(),
                }
                for t in tenants
            ]
        }
    )


@cms_bp.route("/api/tenants", methods=["POST"])
@login_required
@admin_required
def create_tenant() -> flask.Response:
    """Create a new tenant."""
    if not current_user.is_super_admin:
        abort(403)
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip()
    if not name or not slug:
        return api_error("name and slug are required", 400)
    if Tenant.query.filter_by(slug=slug).first():
        return api_error("Slug already exists", 409)
    import uuid as _uuid

    tenant = Tenant(
        id=str(_uuid.uuid4()),
        name=name,
        slug=slug,
        is_active=True,
        tier=data.get("tier", "free"),
    )
    db.session.add(tenant)
    db.session.commit()
    return jsonify(
        {"message": f"Tenant '{name}' created", "tenant": tenant.to_dict()}
    ), 201


@cms_bp.route("/api/tenants/<tenant_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_tenant(tenant_id: str) -> flask.Response:
    """Delete a tenant (super admin only)."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    if not tenant:
        return api_error("Tenant not found", 404)
    db.session.delete(tenant)
    db.session.commit()
    return api_success({}, f"Tenant '{tenant.name}' deleted")


@cms_bp.route("/api/tenants/<tenant_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_tenant(tenant_id: str) -> flask.Response:
    """Toggle tenant active status."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    tenant.is_active = not tenant.is_active
    db.session.commit()
    return jsonify(
        {
            "message": f"Tenant {'activated' if tenant.is_active else 'deactivated'}",
            "is_active": tenant.is_active,
        }
    )


@cms_bp.route("/settings/update-tier", methods=["POST"])
@login_required
def update_tier():
    """Change the current tenant's tier.

    Tenant owners can select free/starter/professional.
    Super admins can set any tier including enterprise.
    """
    if not current_user.is_admin:
        abort(403)

    from ..tier_limits import TIERS, TIER_DISPLAY

    new_tier = (request.form.get("tier") or "").strip().lower()
    if not new_tier:
        flash("No tier specified.", "danger")
        return redirect(url_for("cms.settings", category="plan"))
    if new_tier not in TIERS:
        flash(f"Unknown tier: {new_tier}", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    if new_tier == "enterprise" and not current_user.is_super_admin:
        flash("Only super admins can set the Enterprise tier.", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    if not current_user.is_super_admin and not current_user.is_tenant_owner:
        flash("Only the tenant owner can change the plan.", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    tenant = current_user.tenant
    if not tenant:
        abort(400)

    tenant.tier = new_tier
    db.session.commit()

    flash(
        f"Plan updated to {TIER_DISPLAY.get(new_tier, new_tier.title())}.",
        "success",
    )
    return redirect(url_for("cms.settings", category="plan"))
