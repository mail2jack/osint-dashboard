import logging

from flask import request, render_template
from flask_login import login_required

from . import cms_bp
from ..models import db, AuditLog, User
from ..auth import senior_required

logger = logging.getLogger(__name__)


@cms_bp.route('/audit')
@login_required
@senior_required
def audit_log():
    """View audit log with filtering."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    entity_type = request.args.get('entity_type', '')
    action = request.args.get('action', '')
    user_id = request.args.get('user_id', '')
    case_id = request.args.get('case_id', '')
    search = request.args.get('search', '')

    query = AuditLog.query.options(db.joinedload(AuditLog.user))

    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    if action:
        query = query.filter_by(action=action)
    if user_id:
        query = query.filter_by(user_id=user_id)
    if case_id:
        query = query.filter_by(case_id=case_id)
    if search:
        query = query.filter(
            db.or_(
                AuditLog.description.ilike(f'%{search}%'),
                AuditLog.action.ilike(f'%{search}%')
            )
        )

    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Get filter options for dropdowns
    users = User.query.filter_by(is_active=True).all()
    entity_types = db.session.query(AuditLog.entity_type).distinct().all()
    entity_types = [e[0] for e in entity_types]
    actions = db.session.query(AuditLog.action).distinct().all()
    actions = [a[0] for a in actions]

    return render_template('cms/audit/log.html',
                           logs=pagination.items,
                           pagination=pagination,
                           filters={'entity_type': entity_type, 'action': action,
                                    'user_id': user_id, 'case_id': case_id, 'search': search},
                           users=users,
                           entity_types=entity_types,
                           actions=actions
                           )
