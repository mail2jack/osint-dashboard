import os
import json
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
    session,
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
    Case,
    Subject,
    Client,
    Finding,
    Invoice,
    Payment,
    Document,
    ProrationLog,
    init_default_settings,
)
from ..auth import admin_required, apply_tenant_filter, ensure_tenant_access
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


@cms_bp.route("/tenant-settings")
@login_required
def tenant_settings():
    """Per-tenant settings page (tenant owner / admin)."""
    if not current_user.is_admin:
        abort(403)
    settings_list = (
        TenantSetting.query.filter_by(tenant_id=current_user.tenant_id)
        .order_by(TenantSetting.category, TenantSetting.key)
        .all()
    )
    return render_template(
        "cms/settings/tenant_settings.html",
        settings_list=settings_list,
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
    q = apply_tenant_filter(q, AuditLog)

    if entity_filter:
        q = q.filter(AuditLog.entity_type == entity_filter)
    if user_filter:
        q = q.join(AuditLog.user).filter(User.username.ilike(f"%{user_filter}%"))

    q = q.order_by(AuditLog.timestamp.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    type_query = db.session.query(AuditLog.entity_type).filter(
        AuditLog.action == "read"
    )
    type_query = apply_tenant_filter(type_query, AuditLog)
    entity_types = [r[0] for r in type_query.distinct().all()]

    user_query = User.query.order_by(User.username)
    user_query = apply_tenant_filter(user_query, User)
    users = user_query.all()

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
    q = apply_tenant_filter(q, LoginLog)
    if show_anomalies_only:
        q = q.filter(LoginLog.is_anomaly == True)
    if user_filter:
        q = q.join(LoginLog.user).filter(User.username.ilike(f"%{user_filter}%"))

    q = q.order_by(LoginLog.created_at.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    user_query = User.query.order_by(User.username)
    user_query = apply_tenant_filter(user_query, User)
    users = user_query.all()

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
    ensure_tenant_access(log)
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
    query = LoginLog.query.filter(LoginLog.created_at < cutoff)
    query = apply_tenant_filter(query, LoginLog)
    deleted = query.delete()
    if deleted:
        db.session.commit()
    return api_success({}, f"Deleted {deleted} login log(s)")


@cms_bp.route("/settings/api-keys")
@login_required
@admin_required
def manage_api_keys() -> str:
    """View and manage per-user API keys."""
    from ..models import ApiKey

    key_query = ApiKey.query.order_by(ApiKey.created_at.desc())
    key_query = apply_tenant_filter(key_query, ApiKey)
    keys = key_query.all()
    user_query = User.query.order_by(User.username)
    user_query = apply_tenant_filter(user_query, User)
    users = user_query.all()

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
    ensure_tenant_access(user)

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
    ensure_tenant_access(key)
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
    ensure_tenant_access(key)
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
    ensure_tenant_access(key)
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
    tenants_list = [
        {
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "join_code": t.join_code,
            "domain": t.domain,
            "tier": t.tier,
            "is_active": t.is_active,
            "owner_id": t.owner_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tenants
    ]
    return render_template(
        "cms/settings/tenants.html",
        tenants=tenants,
        user_counts=user_counts,
        tenants_json=json.dumps(tenants_list),
    )


@cms_bp.route("/tenants/<tenant_id>")
@login_required
@admin_required
def tenant_detail(tenant_id: str) -> str:
    """Tenant detail page (super admin only)."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)

    users = (
        User.query.filter_by(tenant_id=tenant_id).order_by(User.created_at.desc()).all()
    )
    case_count = Case.query.filter_by(tenant_id=tenant_id).count()
    subject_count = Subject.query.filter_by(tenant_id=tenant_id).count()
    client_count = Client.query.filter_by(tenant_id=tenant_id).count()
    finding_count = Finding.query.filter_by(tenant_id=tenant_id).count()
    document_count = Document.query.filter_by(
        tenant_id=tenant_id, is_deleted=False
    ).count()

    from ..tier_limits import get_tier_limits, check_storage_limit

    tier_limits = get_tier_limits(tenant.tier)
    storage_ok, storage_used_mb, storage_max_mb = check_storage_limit(
        tenant.id, tenant.tier
    )

    invoices = (
        Invoice.query.filter_by(tenant_id=tenant_id, is_deleted=False)
        .order_by(Invoice.issue_date.desc())
        .limit(10)
        .all()
    )
    payments = (
        Payment.query.filter_by(tenant_id=tenant_id)
        .order_by(Payment.payment_date.desc())
        .limit(10)
        .all()
    )
    proration_logs = (
        ProrationLog.query.filter_by(tenant_id=tenant_id)
        .order_by(ProrationLog.created_at.desc())
        .limit(20)
        .all()
    )

    recent_logs = (
        AuditLog.query.filter_by(tenant_id=tenant_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "cms/settings/tenant_detail.html",
        tenant=tenant,
        users=users,
        case_count=case_count,
        subject_count=subject_count,
        client_count=client_count,
        finding_count=finding_count,
        document_count=document_count,
        tier_limits=tier_limits,
        storage_used_mb=storage_used_mb,
        storage_max_mb=storage_max_mb,
        invoices=invoices,
        payments=payments,
        recent_logs=recent_logs,
        user_counts={},
        proration_logs=proration_logs,
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
    import secrets
    import string

    alphabet = string.ascii_uppercase + string.digits
    while True:
        join_code = "".join(secrets.choice(alphabet) for _ in range(8))
        if not Tenant.query.filter_by(join_code=join_code).first():
            break

    tenant = Tenant(
        id=str(_uuid.uuid4()),
        name=name,
        slug=slug,
        join_code=join_code,
        is_active=True,
        tier=data.get("tier", "free"),
    )
    db.session.add(tenant)
    db.session.commit()
    return jsonify(
        {"message": f"Tenant '{name}' created", "tenant": tenant.to_dict()}
    ), 201


@cms_bp.route("/api/tenants/<tenant_id>", methods=["PUT"])
@login_required
@admin_required
def update_tenant(tenant_id: str) -> flask.Response:
    """Update tenant name, slug, domain, or tier."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip()
    if name:
        tenant.name = name
    if slug:
        existing = Tenant.query.filter(
            Tenant.slug == slug, Tenant.id != tenant_id
        ).first()
        if existing:
            return api_error("Slug already exists", 409)
        tenant.slug = slug
    if "domain" in data:
        tenant.domain = data.get("domain", "").strip() or None
    if data.get("tier"):
        tenant.tier = data["tier"]
    tenant.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(
        {"message": f"Tenant '{tenant.name}' updated", "tenant": tenant.to_dict()}
    )


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


@cms_bp.route("/switch-tenant/<tenant_id>", methods=["POST"])
@login_required
@admin_required
def switch_tenant(tenant_id: str) -> flask.Response:
    """Super-admin: switch tenant context to inspect tenant data."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    session["switched_tenant_id"] = tenant.id
    flash(f"Switched to tenant: {tenant.name}", "info")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/switch-tenant/clear", methods=["POST"])
@login_required
def clear_switch_tenant() -> flask.Response:
    """Super-admin: return to own tenant context."""
    session.pop("switched_tenant_id", None)
    flash("Returned to your own tenant context.", "info")
    return redirect(url_for("cms.dashboard"))


# =============================================================================
# Tenant-scoped user management (super admin)
# =============================================================================


@cms_bp.route("/tenants/<tenant_id>/invite", methods=["POST"])
@login_required
@admin_required
def tenant_invite_user(tenant_id: str) -> flask.Response:
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    email = (request.form.get("email") or "").strip().lower()
    role = request.form.get("role", "investigator").strip()
    if not email or "@" not in email:
        flash("Invalid email address.", "danger")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))

    if User.query.filter(
        db.func.lower(User.email) == email, User.tenant_id == tenant.id
    ).first():
        flash("A user with this email already exists in this tenant.", "danger")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))

    from ..models import Invitation

    inv = Invitation.create_invitation(
        tenant_id=tenant.id,
        email=email,
        role=role,
        invited_by_id=current_user.id,
    )
    db.session.commit()

    accept_url = url_for("auth.accept_invite", token=inv.token, _external=True)
    from ..email_utils import is_smtp_configured, send_email

    if is_smtp_configured():
        subject = f"You've been invited to {tenant.name}"
        send_email(
            email,
            subject,
            f"<p>Accept: <a href='{accept_url}'>{accept_url}</a></p>",
            f"Accept: {accept_url}",
        )
        flash(f"Invitation sent to {email}.", "success")
    else:
        flash(f"Invitation link: {accept_url}", "info")

    return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))


@cms_bp.route("/tenants/<tenant_id>/users/<user_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def tenant_deactivate_user(tenant_id: str, user_id: str) -> flask.Response:
    if not current_user.is_super_admin:
        abort(403)
    db.session.get(Tenant, tenant_id) or abort(404)
    user = db.session.get(User, user_id) or abort(404)
    if user.tenant_id != tenant_id:
        abort(404)
    if user.id == current_user.id:
        flash("Cannot deactivate yourself.", "danger")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))
    user.is_active = False
    AuditLog.log(
        user_id=current_user.id,
        action="deactivate",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Super-admin deactivated user {user.username} in tenant {tenant_id}",
    )
    db.session.commit()
    flash(f"User {user.full_name} deactivated.", "info")
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))


@cms_bp.route("/tenants/<tenant_id>/users/<user_id>/reactivate", methods=["POST"])
@login_required
@admin_required
def tenant_reactivate_user(tenant_id: str, user_id: str) -> flask.Response:
    if not current_user.is_super_admin:
        abort(403)
    db.session.get(Tenant, tenant_id) or abort(404)
    user = db.session.get(User, user_id) or abort(404)
    if user.tenant_id != tenant_id:
        abort(404)
    user.is_active = True
    AuditLog.log(
        user_id=current_user.id,
        action="reactivate",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Super-admin reactivated user {user.username} in tenant {tenant_id}",
    )
    db.session.commit()
    flash(f"User {user.full_name} reactivated.", "success")
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))


@cms_bp.route("/tenants/<tenant_id>/users/<user_id>/role", methods=["POST"])
@login_required
@admin_required
def tenant_change_role(tenant_id: str, user_id: str) -> flask.Response:
    if not current_user.is_super_admin:
        abort(403)
    db.session.get(Tenant, tenant_id) or abort(404)
    user = db.session.get(User, user_id) or abort(404)
    if user.tenant_id != tenant_id:
        abort(404)
    new_role = request.form.get("role", "").strip()
    valid = {
        "admin",
        "senior_investigator",
        "investigator",
        "junior_investigator",
        "viewer",
    }
    if new_role not in valid:
        flash("Invalid role.", "danger")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))
    old_role = user.role
    user.role = new_role
    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Super-admin changed role from {old_role} to {new_role} for user {user.username} in tenant {tenant_id}",
    )
    db.session.commit()
    flash(f"Role for {user.full_name} changed to {new_role}.", "success")
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))


@cms_bp.route("/tenants/<tenant_id>/users/<user_id>/reset-2fa", methods=["POST"])
@login_required
@admin_required
def tenant_reset_2fa(tenant_id: str, user_id: str) -> flask.Response:
    if not current_user.is_super_admin:
        abort(403)
    db.session.get(Tenant, tenant_id) or abort(404)
    user = db.session.get(User, user_id) or abort(404)
    if user.tenant_id != tenant_id:
        abort(404)
    user.totp_secret = None
    user.totp_enabled = False
    user.backup_codes = None
    AuditLog.log(
        user_id=current_user.id,
        action="2fa_reset",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Super-admin reset 2FA for user {user.username} in tenant {tenant_id}",
    )
    db.session.commit()
    flash(f"2FA reset for {user.full_name}.", "success")
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))


@cms_bp.route("/tenants/<tenant_id>/billing-portal")
@login_required
@admin_required
def tenant_billing_portal(tenant_id: str) -> flask.Response:
    """Super-admin: open Stripe customer portal for a specific tenant."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    if not tenant.stripe_customer_id:
        flash("No Stripe customer ID for this tenant.", "warning")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))
    try:
        import stripe

        stripe.api_key = current_app.config.get("STRIPE_SECRET_KEY")
        session = stripe.billing_portal.Session.create(
            customer=tenant.stripe_customer_id,
            return_url=url_for(
                "cms.tenant_detail", tenant_id=tenant.id, _external=True
            ),
        )
        return redirect(session.url, code=303)
    except Exception as e:
        logger.exception("Stripe portal error for tenant %s", tenant_id)
        flash(f"Billing portal error: {e}", "danger")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))


@cms_bp.route("/api/tenant-settings/upload-logo", methods=["POST"])
@login_required
def upload_tenant_logo():
    """Upload logo for the current tenant."""
    if not current_user.is_admin:
        abort(403)
    if "file" not in request.files:
        return api_error("No file provided", 400)
    file = request.files["file"]
    if not file.filename:
        return api_error("No file selected", 400)

    import os as _os

    ext = _os.path.splitext(file.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        return api_error("Unsupported file type. Use PNG, JPG, GIF, SVG or WebP.", 400)

    logo_dir = _os.path.join(current_app.root_path, "static", "uploads", "tenant_logos")
    _os.makedirs(logo_dir, exist_ok=True)

    filename = f"tenant_{current_user.tenant_id}{ext}"
    filepath = _os.path.join(logo_dir, filename)
    file.save(filepath)

    TenantSetting.set(
        "app_logo", filename, category="branding", description="Tenant logo filename"
    )
    db.session.commit()

    return jsonify(
        {
            "filename": filename,
            "url": url_for("static", filename=f"uploads/tenant_logos/{filename}"),
        }
    )


@cms_bp.route("/api/tenant-settings/delete-logo", methods=["POST"])
@login_required
def delete_tenant_logo():
    """Delete tenant logo."""
    if not current_user.is_admin:
        abort(403)
    logo = TenantSetting.get("app_logo", tenant_id=current_user.tenant_id)
    if logo:
        import os as _os

        filepath = _os.path.join(
            current_app.root_path, "static", "uploads", "tenant_logos", logo
        )
        if _os.path.isfile(filepath):
            try:
                _os.remove(filepath)
            except OSError:
                pass
        existing = TenantSetting.query.filter_by(
            tenant_id=current_user.tenant_id, key="app_logo"
        ).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
    return jsonify({"status": "ok"})


@cms_bp.route("/tenants/<tenant_id>/settings", methods=["POST"])
@login_required
@admin_required
def tenant_update_settings(tenant_id: str) -> flask.Response:
    """Super-admin: update tenant name/slug/domain."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    name = (request.form.get("name") or "").strip()
    slug = (request.form.get("slug") or "").strip()
    domain = (request.form.get("domain") or "").strip()

    if name:
        tenant.name = name
    if slug:
        existing = Tenant.query.filter(
            Tenant.slug == slug, Tenant.id != tenant.id
        ).first()
        if existing:
            flash("Slug already in use.", "danger")
            return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))
        tenant.slug = slug
    if domain:
        existing = Tenant.query.filter(
            Tenant.domain == domain, Tenant.id != tenant.id
        ).first()
        if existing:
            flash("Domain already in use.", "danger")
            return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))
        tenant.domain = domain
    else:
        tenant.domain = None

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="tenant",
        entity_id=tenant.id,
        ip_address=request.remote_addr,
        description=f"Super-admin updated tenant {tenant.name} settings",
    )
    db.session.commit()
    flash("Tenant settings updated.", "success")
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))


@cms_bp.route("/tenants/<tenant_id>/toggle-active", methods=["POST"])
@login_required
@admin_required
def tenant_toggle_active(tenant_id: str) -> flask.Response:
    """Super-admin: toggle tenant active/inactive."""
    if not current_user.is_super_admin:
        abort(403)
    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    tenant.is_active = not tenant.is_active
    action = "activated" if tenant.is_active else "deactivated"
    AuditLog.log(
        user_id=current_user.id,
        action=action,
        entity_type="tenant",
        entity_id=tenant.id,
        ip_address=request.remote_addr,
        description=f"Super-admin {action} tenant {tenant.name}",
    )
    db.session.commit()
    flash(f"Tenant {action}.", "success")
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant.id))


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


@cms_bp.route("/api/settings/upload-logo", methods=["POST"])
@login_required
@admin_required
def upload_logo():
    if "logo" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["logo"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    from ..image_validation import validate_image_file

    is_valid, ext = validate_image_file(file)
    if not is_valid:
        return jsonify(
            {"error": "Invalid image file. Allowed: PNG, JPEG, GIF, WebP"}
        ), 400

    logo_dir = os.path.join(current_app.root_path, "static", "uploads", "logo")
    os.makedirs(logo_dir, exist_ok=True)

    import uuid

    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(logo_dir, filename)
    file.seek(0)
    file.save(filepath)

    setting = Setting.query.filter_by(key="app_logo").first()
    if setting and setting.value:
        old_file = os.path.join(logo_dir, setting.value)
        if os.path.isfile(old_file):
            try:
                os.remove(old_file)
            except OSError as e:
                logger.warning("Could not remove old logo: %s", e)

    if setting:
        setting.value = filename
        setting.updated_at = datetime.now(timezone.utc)
    else:
        setting = Setting(key="app_logo", value=filename, category="appearance")
        db.session.add(setting)
    db.session.commit()

    return jsonify({"filename": filename})


@cms_bp.route("/api/settings/delete-logo", methods=["POST"])
@login_required
@admin_required
def delete_logo():
    setting = Setting.query.filter_by(key="app_logo").first()
    if not setting or not setting.value:
        return jsonify({"error": "No logo set"}), 404

    logo_dir = os.path.join(current_app.root_path, "static", "uploads", "logo")
    filepath = os.path.join(logo_dir, setting.value)
    if os.path.isfile(filepath):
        try:
            os.remove(filepath)
        except OSError as e:
            logger.warning("Could not remove logo file: %s", e)

    setting.value = ""
    setting.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"status": "ok"})


@cms_bp.route("/tenants/<tenant_id>/export")
@login_required
def tenant_export(tenant_id: str) -> flask.Response:
    """Export all tenant data as a ZIP file (super-admin only)."""
    if not current_user.is_super_admin:
        abort(403)

    import io
    import zipfile

    tenant = db.session.get(Tenant, tenant_id) or abort(404)
    from ..tier_limits import check_feature

    if not check_feature("export", tenant_id=tenant_id):
        flash("Export feature is not enabled for this tenant's plan.", "warning")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Tenant info
        zf.writestr("tenant.json", json.dumps(tenant.to_dict(), indent=2, default=str))

        # Users
        users = User.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "users.json",
            json.dumps([u.to_dict() for u in users], indent=2, default=str),
        )

        # Cases
        cases = Case.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "cases.json",
            json.dumps([c.to_dict() for c in cases], indent=2, default=str),
        )

        # Subjects
        subjects = Subject.query.filter_by(tenant_id=tenant_id).all()
        for s in subjects:
            try:
                s.decrypt_identifiers()
            except Exception:
                pass
        zf.writestr(
            "subjects.json",
            json.dumps([s.to_dict() for s in subjects], indent=2, default=str),
        )

        # Clients
        clients = Client.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "clients.json",
            json.dumps([c.to_dict() for c in clients], indent=2, default=str),
        )

        # Findings
        findings = Finding.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "findings.json",
            json.dumps([f.to_dict() for f in findings], indent=2, default=str),
        )

        # Financial records
        from ..models import FinancialRecord

        fins = FinancialRecord.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "financial_records.json",
            json.dumps([f.to_dict() for f in fins], indent=2, default=str),
        )

        # Audit logs
        logs = AuditLog.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "audit_logs.json",
            json.dumps([l.to_dict() for l in logs], indent=2, default=str),
        )

        # Screenshots (metadata + files)
        from ..models import Screenshot

        screenshots = Screenshot.query.filter_by(tenant_id=tenant_id).all()
        zf.writestr(
            "screenshots.json",
            json.dumps([s.to_dict() for s in screenshots], indent=2, default=str),
        )
        for ss in screenshots:
            if ss.storage_path:
                file_path = os.path.join(
                    current_app.root_path, "static", ss.storage_path
                )
                if os.path.isfile(file_path):
                    arcname = f"screenshots/{ss.id}_{ss.filename}"
                    zf.write(file_path, arcname)

        # Documents (metadata + files)
        docs = Document.query.filter_by(tenant_id=tenant_id, is_deleted=False).all()
        zf.writestr(
            "documents.json",
            json.dumps([d.to_dict() for d in docs], indent=2, default=str),
        )
        for doc in docs:
            if doc.storage_path:
                file_path = os.path.join(
                    current_app.root_path, "static", doc.storage_path
                )
                if os.path.isfile(file_path):
                    arcname = f"documents/{doc.id}_{doc.filename}"
                    zf.write(file_path, arcname)

    buf.seek(0)
    safe_name = tenant.slug.replace("/", "_").replace("\\", "_")
    return flask.send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{safe_name}-export.zip",
    )


@cms_bp.route("/tenants/<tenant_id>/gdpr-delete", methods=["GET", "POST"])
@login_required
def tenant_gdpr_delete(tenant_id: str):
    """GDPR-compliant tenant deletion with confirmation (super-admin only)."""
    if not current_user.is_super_admin:
        abort(403)

    tenant = db.session.get(Tenant, tenant_id) or abort(404)

    if request.method == "GET":
        from ..models import User, Case, Subject, Client, Finding, Document

        counts = {
            "users": User.query.filter_by(tenant_id=tenant_id).count(),
            "cases": Case.query.filter_by(tenant_id=tenant_id).count(),
            "subjects": Subject.query.filter_by(tenant_id=tenant_id).count(),
            "clients": Client.query.filter_by(tenant_id=tenant_id).count(),
            "findings": Finding.query.filter_by(tenant_id=tenant_id).count(),
            "documents": Document.query.filter_by(
                tenant_id=tenant_id, is_deleted=False
            ).count(),
        }
        return render_template(
            "cms/settings/tenant_gdpr_delete.html",
            tenant=tenant,
            counts=counts,
        )

    # POST — perform deletion
    confirm_name = request.form.get("confirm_name", "").strip()
    if confirm_name != tenant.name:
        flash("Tenant name does not match. Deletion cancelled.", "error")
        return redirect(url_for("cms.tenant_gdpr_delete", tenant_id=tenant_id))

    # Log the deletion before purging
    from ..data_retention import _purge_single_tenant

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="tenant",
        entity_id=tenant_id,
        description=f"GDPR deletion of tenant '{tenant.name}' ({tenant.slug}) by {current_user.full_name}",
        ip_address=request.remote_addr,
    )
    db.session.commit()

    try:
        _purge_single_tenant(tenant, dry_run=False)
        flash(
            f"All data for tenant '{tenant.name}' has been permanently deleted.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        logger.exception("GDPR deletion failed for tenant %s", tenant_id)
        flash(f"Deletion failed: {e}", "error")

    return redirect(url_for("cms.list_tenants"))
