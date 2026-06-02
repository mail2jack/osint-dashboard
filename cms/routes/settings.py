import logging
from datetime import datetime, timezone, timedelta

import flask
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Setting, AuditLog, LoginLog, User, init_default_settings
from ..auth import admin_required
from ..validation import validate, SaveSettingsSchema

logger = logging.getLogger(__name__)


@cms_bp.route("/settings")
@login_required
@admin_required
def settings() -> str:
    """Settings management page."""
    category = request.args.get("category", "api_keys")

    categories = {
        "api_keys": {"name": "🔑 API Keys", "icon": "🔑"},
        "search": {"name": "🔍 Search", "icon": "🔍"},
        "general": {"name": "⚙️ General", "icon": "⚙️"},
        "security": {"name": "🔒 Security", "icon": "🔒"},
        "email": {"name": "📧 Email", "icon": "📧"},
        "appearance": {"name": "🎨 Appearance", "icon": "🎨"},
        "feature_flags": {"name": "🚩 Feature Flags", "icon": "🚩"},
    }

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


@cms_bp.route("/api/settings/<setting_id>", methods=["GET"])
@login_required
@admin_required
def get_setting_api(setting_id: str) -> flask.Response:
    """Get a single setting."""
    setting = db.session.get(Setting, setting_id) or abort(404)
    return jsonify(setting.to_dict(include_value=not setting.is_sensitive))


@cms_bp.route("/api/settings", methods=["POST"])
@csrf.exempt
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


@cms_bp.route("/api/settings/<setting_id>/reset", methods=["POST"])
@csrf.exempt
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

    return jsonify({"message": "Setting reset to default"})


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
@csrf.exempt
@login_required
@admin_required
def dismiss_anomaly(log_id: str) -> flask.Response:
    """Mark a login anomaly as reviewed/dismissed."""
    log = db.session.get(LoginLog, log_id) or abort(404)
    log.is_anomaly = False
    log.anomaly_reason = ""
    db.session.commit()
    return jsonify({"message": "Anomaly dismissed"})


@cms_bp.route("/api/login-logs/purge", methods=["POST"])
@csrf.exempt
@login_required
@admin_required
def purge_login_logs() -> flask.Response:
    """Delete login logs older than N days."""
    days = request.json.get("days", 90) if request.is_json else 90
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = LoginLog.query.filter(LoginLog.created_at < cutoff).delete()
    if deleted:
        db.session.commit()
    return jsonify({"message": f"Deleted {deleted} login log(s)"})


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
@csrf.exempt
@login_required
@admin_required
def api_create_api_key() -> flask.Response:
    """Create a new API key for a user."""
    from ..models import ApiKey

    data = request.get_json() or {}
    user_id = data.get("user_id")
    name = data.get("name", "").strip()
    raw_scopes = data.get("scopes", ["read"])

    if not user_id or not name:
        return jsonify({"error": "user_id and name are required"}), 400
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

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
@csrf.exempt
@login_required
@admin_required
def api_deactivate_api_key(key_id: str) -> flask.Response:
    """Deactivate an API key."""
    from ..models import ApiKey

    key = db.session.get(ApiKey, key_id) or abort(404)
    key.is_active = False
    db.session.commit()
    return jsonify({"message": "API key deactivated"})


@cms_bp.route("/api/api-keys/<key_id>/activate", methods=["POST"])
@csrf.exempt
@login_required
@admin_required
def api_activate_api_key(key_id: str) -> flask.Response:
    """Reactivate an API key."""
    from ..models import ApiKey

    key = db.session.get(ApiKey, key_id) or abort(404)
    key.is_active = True
    db.session.commit()
    return jsonify({"message": "API key reactivated"})


@cms_bp.route("/api/api-keys/<key_id>/delete", methods=["POST"])
@csrf.exempt
@login_required
@admin_required
def api_delete_api_key(key_id: str) -> flask.Response:
    """Delete an API key."""
    from ..models import ApiKey

    key = db.session.get(ApiKey, key_id) or abort(404)
    db.session.delete(key)
    db.session.commit()
    return jsonify({"message": "API key deleted"})
