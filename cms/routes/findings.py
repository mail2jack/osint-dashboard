import logging

from flask import request, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Finding, AuditLog
from ..auth import roles_required

logger = logging.getLogger(__name__)


@cms_bp.route('/findings/create', methods=['POST'])
@cms_bp.route('/cases/<case_id>/findings/create', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def create_finding():
    """Create a new finding."""
    data = request.get_json() if request.is_json else request.form

    required = ['case_id', 'title', 'content']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    finding = Finding(
        case_id=data['case_id'],
        subject_id=data.get('subject_id'),
        title=data['title'],
        content=data['content'],
        source_url=data.get('source_url'),
        source_type=data.get('source_type'),
        reliability_score=data.get('reliability_score', 5),
        confidence_level=data.get('confidence_level'),
        finding_type=data.get('finding_type'),
        tags=data.get('tags'),
        created_by=current_user.id
    )

    db.session.add(finding)

    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='finding',
        entity_id=finding.id,
        ip_address=request.remote_addr,
        case_id=data['case_id'],
        description=f"Added finding: {finding.title}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({'message': 'Finding created', 'finding': finding.to_dict()}), 201

    return jsonify({'message': 'Finding created', 'finding': finding.to_dict()})
