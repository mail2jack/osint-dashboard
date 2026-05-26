import logging

import flask
from flask import (
    request, jsonify, render_template, abort
)
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db, Subject, Case, Finding, SocialAccount, AuditLog
)
from .utils import (
    find_similar_subjects, find_similar_clients,
    check_for_exact_match
)

logger = logging.getLogger(__name__)


@cms_bp.route('/subjects')
@login_required
def subjects() -> str:
    """List all subjects with search, filtering, and sorting."""
    page = request.args.get('page', 1, type=int)
    per_page = 30
    search = request.args.get('search', '')
    subject_type = request.args.get('type', '')
    sort = request.args.get('sort', 'name')
    order = request.args.get('order', 'asc')
    fmt = request.args.get('format', '')

    query = Subject.query.filter_by(is_deleted=False)

    if search:
        query = query.outerjoin(SocialAccount, SocialAccount.subject_id == Subject.id).filter(
            db.or_(
                Subject.name.ilike(f'%{search}%'),
                SocialAccount.username.ilike(f'%{search}%'),
            )
        ).distinct()

    if subject_type:
        query = query.filter_by(subject_type=subject_type)

    # Sorting
    sort_columns = {
        'name': Subject.name,
        'type': Subject.subject_type,
        'risk': Subject.risk_score,
    }

    sort_col = sort_columns.get(sort, Subject.name)
    if order == 'desc':
        sort_col = sort_col.desc()

    # JSON format for API calls (subject picker dropdown)
    if fmt == 'json':
        search_q = request.args.get('q', '').strip()
        if search_q:
            query = query.filter(Subject.name.ilike(f'%{search_q}%'))
        subjects_list = query.order_by(sort_col).limit(200).all()
        return jsonify({
            'subjects': [{'id': s.id, 'name': s.name, 'type': s.subject_type} for s in subjects_list],
            'total': len(subjects_list),
            'has_more': query.order_by(sort_col).count() > 200
        })

    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('cms/subjects/list.html',
                           subjects=pagination.items,
                           pagination=pagination,
                           filters={'search': search, 'type': subject_type,
                                    'sort': sort, 'order': order}
                           )


@cms_bp.route('/subjects/<subject_id>')
@login_required
def view_subject(subject_id: str) -> str:
    """View subject details."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    subject.decrypt_identifiers()
    subject.vessel_data = subject.vessel_data or {}
    for addr in subject.addresses:
        addr.decrypt_fields()
    for c in subject.contacts:
        c.decrypt_fields()

    # Paginated findings
    findings_page = request.args.get('findings_page', 1, type=int)
    findings_per_page = 20
    findings_pagination = subject.findings.filter_by(
        is_deleted=False
    ).order_by(Finding.created_at.desc()).paginate(
        page=findings_page, per_page=findings_per_page, error_out=False
    )

    # Get linked cases — use association table directly to avoid N+1
    from ..models import case_subjects
    linked_case_ids = [
        row.case_id for row in db.session.query(case_subjects.c.case_id).filter(
            case_subjects.c.subject_id == subject.id
        ).all()
    ]
    linked_cases = []
    first_case_id = None
    if linked_case_ids:
        for case in Case.query.filter(Case.id.in_(linked_case_ids), Case.is_deleted.is_(False)).all():
            case_info = {'id': case.id,
                         'case_number': case.case_number, 'title': case.title}
            linked_cases.append(case_info)
            if first_case_id is None:
                first_case_id = case.id

    AuditLog.log(
        user_id=current_user.id,
        action='read',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Viewed subject: {subject.name}"
    )
    db.session.commit()

    return render_template('cms/subjects/view.html',
                           subject=subject,
                           findings=findings_pagination.items,
                           findings_pagination=findings_pagination,
                           linked_cases=linked_cases,
                           first_case_id=first_case_id
                           )


@cms_bp.route('/api/check-duplicate')
@login_required
def check_duplicate() -> flask.Response:
    """Check for duplicate subjects or clients by name (for real-time lookup)."""
    name = request.args.get('name', '').strip()
    entity_type = request.args.get('type', 'subject')  # 'subject' or 'client'

    if len(name) < 2:
        return jsonify({'duplicates': [], 'exact': None})

    if entity_type == 'subject':
        exact = check_for_exact_match(name, 'subject')
        similar = find_similar_subjects(name)[:5]
        return jsonify({'duplicates': similar, 'exact': exact})
    else:
        exact = check_for_exact_match(name, 'client')
        similar = find_similar_clients(name)[:5]
        return jsonify({'duplicates': similar, 'exact': exact})
