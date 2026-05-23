import logging
from datetime import datetime, timezone

from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Setting, AuditLog, init_default_settings
from ..auth import admin_required

logger = logging.getLogger(__name__)


@cms_bp.route('/settings')
@login_required
@admin_required
def settings():
    """Settings management page."""
    category = request.args.get('category', 'api_keys')

    categories = {
        'api_keys': {'name': '🔑 API Keys', 'icon': '🔑'},
        'search': {'name': '🔍 Search', 'icon': '🔍'},
        'general': {'name': '⚙️ General', 'icon': '⚙️'},
        'security': {'name': '🔒 Security', 'icon': '🔒'},
        'email': {'name': '📧 Email', 'icon': '📧'},
        'appearance': {'name': '🎨 Appearance', 'icon': '🎨'},
    }

    settings_list = Setting.query.filter_by(
        category=category,
        is_active=True
    ).order_by(Setting.display_order).all()

    return render_template('cms/settings/index.html',
                           settings_list=settings_list,
                           categories=categories,
                           active_category=category
                           )


@cms_bp.route('/api/settings')
@login_required
@admin_required
def get_settings_api():
    """Get all settings grouped by category (masked values)."""
    categories = request.args.get('category', None)

    query = Setting.query.filter_by(is_active=True)
    if categories:
        query = query.filter_by(category=categories)

    settings_list = query.order_by(
        Setting.category, Setting.display_order).all()

    return jsonify({
        'settings': [s.to_dict(include_value=False) for s in settings_list]
    })


@cms_bp.route('/api/settings/<setting_id>', methods=['GET'])
@login_required
@admin_required
def get_setting_api(setting_id: str):
    """Get a single setting."""
    setting = db.session.get(Setting, setting_id) or abort(404)
    return jsonify(setting.to_dict(include_value=not setting.is_sensitive))


@cms_bp.route('/api/settings', methods=['POST'])
@login_required
@admin_required
def save_settings_api():
    """Save one or more settings."""
    data = request.get_json()

    if not data or 'settings' not in data:
        return jsonify({'error': 'Settings data required'}), 400

    saved_count = 0
    errors = []

    for item in data['settings']:
        setting_id = item.get('id')
        new_value = item.get('value')

        if setting_id:
            setting = db.session.get(Setting, setting_id)
            if setting:
                old_value = setting.get_masked_value() if setting.is_sensitive else setting.value
                setting.value = new_value
                setting.updated_at = datetime.now(timezone.utc)

                AuditLog.log(
                    user_id=current_user.id,
                    action='setting_updated',
                    entity_type='setting',
                    entity_id=setting_id,
                    changes={'value': {
                        'old': old_value, 'new': '***MASKED***' if setting.is_sensitive else new_value}},
                    ip_address=request.remote_addr,
                    description=f"Updated setting: {setting.key}"
                )
                saved_count += 1
        else:
            errors.append(
                f"Missing setting ID for: {item.get('key', 'unknown')}")

    db.session.commit()

    # Reinitialize default settings if needed
    try:
        init_default_settings()
    except Exception as e:
        logger.warning(f"Failed to initialize default settings ({type(e).__name__}): {e}")

    return jsonify({
        'message': f'Saved {saved_count} setting(s)',
        'saved': saved_count,
        'errors': errors
    })


@cms_bp.route('/api/settings/<setting_id>/reset', methods=['POST'])
@login_required
@admin_required
def reset_setting_api(setting_id: str):
    """Reset a setting to its default value."""
    setting = db.session.get(Setting, setting_id) or abort(404)

    # Remove the setting (will be recreated by init_default_settings)
    setting.is_active = False
    setting.updated_at = datetime.now(timezone.utc)

    AuditLog.log(
        user_id=current_user.id,
        action='setting_reset',
        entity_type='setting',
        entity_id=setting_id,
        ip_address=request.remote_addr,
        description=f"Reset setting: {setting.key}"
    )

    db.session.commit()

    # Reinitialize to get default value
    init_default_settings()

    return jsonify({'message': 'Setting reset to default'})
