import json
import logging
from datetime import datetime, date, timezone, timedelta
from itertools import groupby

from flask import (
    request, jsonify, render_template,
    redirect, url_for, flash, abort
)
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db, Case, Client, Subject, Finding, FinancialRecord,
    AuditLog, User, CaseStatus, CasePriority, Comment,
    Reminder, Document
)
from ..auth import roles_required, admin_required, senior_required, case_access_required, case_edit_required
from ..encryption_utils import encryptor

logger = logging.getLogger(__name__)


@cms_bp.route('/cases')
@login_required
def cases():
    """List all cases with filtering, sorting, and search."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    status = request.args.get('status', '')
    priority = request.args.get('priority', '')
    search = request.args.get('search', '')
    client_filter = request.args.get('client', '')
    assigned = request.args.get('assigned', '')
    sort = request.args.get('sort', 'case_number')
    order = request.args.get('order', 'desc')

    query = Case.query.filter_by(is_deleted=False).join(Client)

    if status:
        query = query.filter(Case.status == status)
    else:
        query = query.filter(Case.status != CaseStatus.CLOSED.value)

    if priority:
        query = query.filter(Case.priority == priority)

    case_type_filter = request.args.get('case_type', '')
    if case_type_filter:
        query = query.filter(Case.case_type.like(f'{case_type_filter}|%'))

    if client_filter:
        query = query.filter(Client.id == client_filter)

    if search:
        query = query.filter(
            db.or_(
                Case.title.ilike(f'%{search}%'),
                Case.case_number.ilike(f'%{search}%'),
                Case.description.ilike(f'%{search}%'),
                Client.name.ilike(f'%{search}%')
            )
        )

    if assigned == 'me' and not current_user.is_admin:
        from ..models import case_assignments
        assigned_ids = db.session.query(case_assignments.c.case_id).filter(
            case_assignments.c.user_id == current_user.id
        ).all()
        assigned_ids = [row[0] for row in assigned_ids]

        query = query.filter(
            db.or_(
                Case.assigned_to == current_user.id,
                Case.id.in_(assigned_ids) if assigned_ids else False
            )
        )

    sort_columns = {
        'case_number': Case.case_number,
        'title': Case.title,
        'client': Client.name,
        'priority': Case.priority,
        'status': Case.status,
        'created_at': Case.created_at,
        'updated_at': Case.updated_at,
    }

    sort_col = sort_columns.get(sort, Case.case_number)
    if order == 'desc':
        sort_col = sort_col.desc()

    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )

    clients = Client.query.filter_by(is_deleted=False, is_active=True).all()

    return render_template('cms/cases/list.html',
                           cases=pagination.items,
                           pagination=pagination,
                           clients=clients,
                           filters={'status': status, 'priority': priority, 'search': search, 'client': client_filter,
                                    'assigned': assigned, 'sort': sort, 'order': order, 'case_type': case_type_filter}
                           )


@cms_bp.route('/cases/<case_id>')
@login_required
@case_access_required
def view_case(case_id: str):
    """View case details with subjects, findings, and financials."""
    case = db.session.get(Case, case_id) or abort(404)
    subjects = case.subjects.all()

    findings_page = request.args.get('findings_page', 1, type=int)
    findings_per_page = 20
    findings_pagination = case.findings.filter_by(is_deleted=False).order_by(
        Finding.created_at.desc()
    ).paginate(page=findings_page, per_page=findings_per_page, error_out=False)

    financials = case.financial_records.filter_by(is_deleted=False).order_by(
        FinancialRecord.transaction_date.desc()).all()

    documents = Document.query.filter_by(
        case_id=case_id, is_deleted=False).order_by(Document.created_at.desc()).all()

    case_reminders = Reminder.query.filter_by(
        case_id=case_id,
        is_deleted=False
    ).order_by(Reminder.reminder_date.asc()).all()

    linked_ids = [s.id for s in subjects]
    all_subjects = Subject.query.filter(Subject.is_deleted == False).all()
    available_subjects = [s for s in all_subjects if s.id not in linked_ids]

    AuditLog.log(
        user_id=current_user.id,
        action='read',
        entity_type='case',
        entity_id=case_id,
        ip_address=request.remote_addr,
        description=f"Viewed case: {case.case_number} - {case.title}"
    )
    db.session.commit()

    return render_template('cms/cases/view.html',
                           case=case,
                           subjects=subjects,
                           findings=findings_pagination.items,
                           findings_pagination=findings_pagination,
                           financials=financials,
                           documents=documents,
                           all_subjects=available_subjects,
                           case_reminders=case_reminders
                           )


@cms_bp.route('/cases/<case_id>/set-parent', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
@case_edit_required
def set_case_parent(case_id: str):
    """Set the parent case for a case."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.get_json() if request.is_json else request.form

    parent_id = data.get('parent_case_id')

    if parent_id:
        parent = db.session.get(Case, parent_id)
        if not parent or parent.is_deleted:
            return jsonify({'error': 'Parent case not found'}), 404

        if parent_id == case_id:
            return jsonify({'error': 'A case cannot be its own parent'}), 400

        current = parent
        while current and current.parent_case_id:
            if current.parent_case_id == case_id:
                return jsonify({'error': 'This would create a circular reference'}), 400
            current = current.parent_case

        old_parent_id = case.parent_case_id
        case.parent_case_id = parent_id

        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='case',
            entity_id=case_id,
            changes={'parent_case': {'old': old_parent_id, 'new': parent_id}},
            ip_address=request.remote_addr,
            description=f"Set parent case for {case.case_number} to {parent.case_number}"
        )
        db.session.commit()

        return jsonify({
            'message': 'Parent case set',
            'parent_case': {'id': parent.id, 'case_number': parent.case_number, 'title': parent.title}
        })
    else:
        old_parent_id = case.parent_case_id
        case.parent_case_id = None

        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='case',
            entity_id=case_id,
            changes={'parent_case': {'old': old_parent_id, 'new': None}},
            ip_address=request.remote_addr,
            description=f"Removed parent case from {case.case_number}"
        )
        db.session.commit()

        return jsonify({'message': 'Parent case removed'})


@cms_bp.route('/api/cases/search')
@login_required
def search_cases():
    """Search cases for linking (excludes the current case)."""
    q = request.args.get('q', '')
    exclude_id = request.args.get('exclude_id', '')

    query = Case.query.filter_by(is_deleted=False)

    if q:
        query = query.filter(
            db.or_(
                Case.case_number.ilike(f'%{q}%'),
                Case.title.ilike(f'%{q}%')
            )
        )

    if exclude_id:
        query = query.filter(Case.id != exclude_id)

    cases = query.order_by(Case.created_at.desc()).limit(20).all()

    return jsonify({
        'cases': [{'id': c.id, 'case_number': c.case_number, 'title': c.title, 'status': c.status} for c in cases]
    })


@cms_bp.route('/api/cases/<case_id>/hierarchy')
@login_required
def get_case_hierarchy_api(case_id: str):
    """Get case hierarchy (parent and children) via API."""
    case = db.session.get(Case, case_id) or abort(404)
    return jsonify({
        'parent': {
            'id': case.parent_case.id,
            'case_number': case.parent_case.case_number,
            'title': case.parent_case.title
        } if case.parent_case else None,
        'children': [
            {'id': c.id, 'case_number': c.case_number,
                'title': c.title, 'status': c.status}
            for c in case.child_cases.filter_by(is_deleted=False)
        ]
    })


@cms_bp.route('/api/cases/<case_id>/audit-log')
@login_required
def get_case_audit_log_api(case_id: str):
    """Get audit log for a case via API."""
    db.session.get(Case, case_id) or abort(404)
    logs = AuditLog.query.filter_by(
        entity_type='case',
        entity_id=case_id
    ).order_by(AuditLog.created_at.desc()).limit(50).all()

    return jsonify({
        'logs': [
            {
                'action': log.action,
                'description': log.description,
                'user': log.user.full_name if log.user else 'System',
                'created_at': log.created_at.strftime('%Y-%m-%d %H:%M') if log.created_at else '',
                'changes': log.changes
            }
            for log in logs
        ]
    })


@cms_bp.route('/cases/<case_id>/timeline')
@login_required
@case_access_required
def case_timeline(case_id: str):
    """Get timeline of all events for a case."""
    case = db.session.get(Case, case_id) or abort(404)

    timeline = []

    timeline.append({
        'timestamp': case.created_at,
        'type': 'create',
        'icon': '📁',
        'title': 'Case Created',
        'description': f'{case.case_number} - {case.title}',
        'user': None,
        'details': f'Client: {case.client.name if case.client else "N/A"}'
    })

    status_logs = AuditLog.query.filter(
        AuditLog.entity_type == 'case',
        AuditLog.entity_id == case_id,
        AuditLog.action.in_(['update', 'status_change'])
    ).order_by(AuditLog.timestamp.asc()).all()

    for log in status_logs:
        if log.changes_made and 'status' in str(log.changes_made):
            timeline.append({
                'timestamp': log.timestamp,
                'type': 'status',
                'icon': '🔄',
                'title': 'Status Changed',
                'description': log.description or 'Case status updated',
                'user': log.user,
                'details': log.changes_made
            })

    subject_add_logs = AuditLog.query.filter(
        AuditLog.case_id == case_id,
        AuditLog.action == 'create',
        AuditLog.entity_type == 'case_subject'
    ).order_by(AuditLog.timestamp.asc()).all()

    for log in subject_add_logs:
        timeline.append({
            'timestamp': log.timestamp,
            'type': 'subject',
            'icon': '👤',
            'title': 'Subject Added',
            'description': log.description or 'Subject linked to case',
            'user': log.user,
            'details': None
        })

    for finding in case.findings.filter_by(is_deleted=False).order_by(Finding.created_at.asc()).all():
        timeline.append({
            'timestamp': finding.created_at,
            'type': 'finding',
            'icon': '🔍',
            'title': 'Finding Added',
            'description': finding.title[:100] + ('...' if len(finding.title) > 100 else ''),
            'user': finding.author,
            'details': f'Source: {finding.source_type or "manual"}'
        })

    osint_logs = AuditLog.query.filter(
        AuditLog.case_id == case_id,
        AuditLog.action.in_(['osint_search_start', 'osint_search_cancel'])
    ).order_by(AuditLog.timestamp.asc()).all()

    for log in osint_logs:
        icon = '🔍' if log.action == 'osint_search_start' else '⏹️'
        timeline.append({
            'timestamp': log.timestamp,
            'type': 'osint',
            'icon': icon,
            'title': 'OSINT Search' if log.action == 'osint_search_start' else 'OSINT Search Cancelled',
            'description': log.description or 'OSINT search performed',
            'user': log.user,
            'details': None
        })

    for fin in case.financial_records.filter_by(is_deleted=False).order_by(FinancialRecord.created_at.asc()).all():
        timeline.append({
            'timestamp': fin.created_at,
            'type': 'financial',
            'icon': '💰',
            'title': 'Financial Record',
            'description': f'{fin.currency} {fin.amount} - {fin.transaction_type or "Transaction"}',
            'user': None,
            'details': f'Source: {fin.source or "N/A"}'
        })

    if case.reopened_at:
        timeline.append({
            'timestamp': case.reopened_at,
            'type': 'reopen',
            'icon': '↩️',
            'title': 'Case Reopened',
            'description': f'Reopened: {case.reopened_reason or "No reason provided"}',
            'user': None,
            'details': None
        })

    if case.actual_end_date:
        timeline.append({
            'timestamp': datetime.combine(case.actual_end_date, datetime.min.time()),
            'type': 'close',
            'icon': '✅',
            'title': 'Case Closed',
            'description': f'Closure: {case.closure_reason or "No reason provided"}',
            'user': None,
            'details': None
        })

    timeline.sort(key=lambda x: x['timestamp'] or datetime.min, reverse=True)

    return jsonify({
        'case_id': case_id,
        'case_number': case.case_number,
        'title': case.title,
        'timeline': [{
            'timestamp': t['timestamp'].isoformat() if t['timestamp'] else None,
            'type': t['type'],
            'icon': t['icon'],
            'title': t['title'],
            'description': t['description'],
            'user_name': t['user'].full_name if t['user'] else 'System',
            'details': t['details']
        } for t in timeline]
    })


@cms_bp.route('/cases/<case_id>/report')
@login_required
@case_access_required
def case_report(case_id: str):
    """Chronological report merging Findings + Comments for a case."""
    case = db.session.get(Case, case_id) or abort(404)
    from_date = request.args.get('from')
    to_date = request.args.get('to')
    subject_filter = request.args.get('subject_id')

    findings_q = Finding.query.filter_by(case_id=case_id, is_deleted=False)
    comments_q = Comment.query.filter_by(case_id=case_id, is_deleted=False)

    if from_date:
        try:
            fd = datetime.strptime(from_date, '%Y-%m-%d')
            findings_q = findings_q.filter(Finding.created_at >= fd)
            comments_q = comments_q.filter(Comment.created_at >= fd)
        except ValueError:
            pass
    if to_date:
        try:
            td = datetime.strptime(to_date, '%Y-%m-%d') + timedelta(days=1)
            findings_q = findings_q.filter(Finding.created_at < td)
            comments_q = comments_q.filter(Comment.created_at < td)
        except ValueError:
            pass
    if subject_filter:
        findings_q = findings_q.filter_by(subject_id=subject_filter)
        comments_q = comments_q.filter_by(subject_id=subject_filter)

    findings = findings_q.order_by(Finding.created_at.asc()).all()
    comments = comments_q.order_by(Comment.created_at.asc()).all()

    entries = []
    for f in findings:
        subject = db.session.get(Subject, f.subject_id) if f.subject_id else None
        entries.append({
            'type': 'finding',
            'icon': '🔍',
            'timestamp': f.created_at,
            'title': f.title,
            'content': f.content,
            'source_type': f.source_type,
            'confidence': f.confidence_level,
            'source_url': f.source_url,
            'author': f.author.full_name if f.author else '-',
            'subject_name': subject.name if subject else '-',
            'subject_id': f.subject_id,
            'finding_type': f.finding_type
        })
    for c in comments:
        subject = db.session.get(Subject, c.subject_id) if c.subject_id else None
        entries.append({
            'type': 'note',
            'icon': '📝' if c.comment_type == 'note' else '💬',
            'timestamp': c.created_at,
            'title': c.comment_type.capitalize(),
            'content': c.content,
            'source_type': c.comment_type,
            'confidence': None,
            'source_url': None,
            'author': c.author.full_name if c.author else '-',
            'subject_name': subject.name if subject else '-',
            'subject_id': c.subject_id,
            'finding_type': None
        })

    entries.sort(key=lambda e: e['timestamp'] or datetime.min)

    grouped = {}
    for e in entries:
        date_key = e['timestamp'].strftime('%Y-%m-%d') if e['timestamp'] else 'Unknown'
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(e)

    subjects = Subject.query.filter(
        Subject.cases.any(id=case_id),
        Subject.is_deleted == False
    ).all()

    return render_template(
        'cms/cases/report.html',
        case=case,
        grouped_entries=grouped,
        subjects=subjects,
        from_date=from_date,
        to_date=to_date,
        subject_filter=subject_filter
    )


@cms_bp.route('/cases/create', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def create_case():
    """Create a new case."""
    clients = Client.query.filter_by(is_deleted=False, is_active=True).all()
    investigators = User.query.filter(
        User.is_active == True,
        User.role.in_(['admin', 'senior_investigator', 'junior_investigator'])
    ).all()

    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form

        required = ['title', 'client_id']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400

        client = db.session.get(Client, data['client_id'])
        if not client or client.is_deleted:
            return jsonify({'error': 'Invalid client'}), 400

        case = Case(
            case_number=Case.generate_case_number(),
            client_id=data['client_id'],
            title=data['title'],
            description=data.get('description'),
            priority=data.get('priority', CasePriority.MEDIUM.value),
            status=CaseStatus.OPEN.value,
            start_date=data.get('start_date', date.today()),
            target_end_date=data.get('target_end_date'),
            case_type=data.get('case_type'),
            jurisdiction=data.get('jurisdiction'),
            tags=data.get('tags'),
            created_by=current_user.id,
            lead_investigator_id=data.get('lead_investigator_id') or None
        )

        db.session.add(case)
        db.session.flush()

        if data.get('assigned_to'):
            case.assigned_to = data['assigned_to']

        if data.get('subject_ids'):
            for subject_id in data['subject_ids']:
                subject = db.session.get(Subject, subject_id)
                if subject:
                    case.subjects.append(subject)

        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='case',
            entity_id=case.id,
            new_values={
                'case_number': case.case_number,
                'title': case.title,
                'client_id': case.client_id
            },
            ip_address=request.remote_addr,
            case_id=case.id,
            description=f"Created case: {case.case_number} - {case.title}"
        )
        db.session.commit()

        if request.is_json:
            return jsonify({'message': 'Case created', 'case': case.to_dict()}), 201

        flash(f'Case {case.case_number} created successfully.', 'success')
        return redirect(url_for('cms.view_case', case_id=case.id))

    return render_template('cms/cases/create.html',
                           clients=clients,
                           investigators=investigators
                           )


@cms_bp.route('/cases/<case_id>/edit', methods=['GET', 'POST'])
@login_required
@case_access_required
@case_edit_required
def edit_case(case_id: str):
    """Edit case details."""
    case = db.session.get(Case, case_id) or abort(404)

    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        changes = {}

        editable_fields = ['title', 'description', 'priority',
                           'case_type', 'jurisdiction']

        for field in editable_fields:
            if field in data:
                old_value = getattr(case, field)
                new_value = data[field]
                if new_value != old_value:
                    changes[field] = {'old': str(
                        old_value) if old_value else None, 'new': str(new_value)}
                    setattr(case, field, new_value)

        if 'tags' in data:
            tags_value = data['tags']
            if isinstance(tags_value, str):
                new_tags = [t.strip()
                            for t in tags_value.split(',') if t.strip()]
            elif isinstance(tags_value, list):
                new_tags = tags_value
            else:
                new_tags = []

            old_tags = case.tags or []
            if sorted(new_tags) != sorted(old_tags):
                changes['tags'] = {'old': old_tags, 'new': new_tags}
                case.tags = new_tags if new_tags else None

        new_lead = data.get('lead_investigator_id') or None
        if new_lead != case.lead_investigator_id:
            changes['lead_investigator_id'] = {
                'old': case.lead_investigator_id, 'new': new_lead}
            case.lead_investigator_id = new_lead

        if 'target_end_date' in data and data['target_end_date']:
            try:
                from datetime import datetime as dt
                new_date = dt.strptime(
                    data['target_end_date'], '%Y-%m-%d').date()
                old_date = case.target_end_date
                if new_date != old_date:
                    changes['target_end_date'] = {'old': str(
                        old_date) if old_date else None, 'new': str(new_date)}
                    case.target_end_date = new_date
            except ValueError:
                pass

        if data.get('status') and data['status'] != case.status:
            if case.transition_status(data['status'], current_user.id):
                changes['status'] = {'old': case.status, 'new': data['status']}
            else:
                return jsonify({'error': 'Invalid status transition'}), 400

        case.updated_at = datetime.now(timezone.utc)

        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='case',
            entity_id=case_id,
            changes=changes,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Updated case: {case.case_number}"
        )
        db.session.commit()

        if request.is_json:
            return jsonify({'message': 'Case updated', 'case': case.to_dict()})

        flash('Case updated successfully.', 'success')
        return redirect(url_for('cms.view_case', case_id=case.id))

    clients = Client.query.filter_by(is_deleted=False, is_active=True).all()
    investigators = User.query.filter(
        User.is_active == True,
        User.role.in_(['admin', 'senior_investigator', 'junior_investigator'])
    ).all()

    return render_template('cms/cases/edit.html',
                           case=case,
                           clients=clients,
                           investigators=investigators
                           )


@cms_bp.route('/cases/<case_id>/transition', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def transition_case(case_id: str):
    """Transition case to a new status."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.get_json() if request.is_json else request.form

    new_status = data.get('status')
    if not new_status:
        return jsonify({'error': 'Status is required'}), 400

    old_status = case.status

    if new_status == CaseStatus.CLOSED.value:
        reason = data.get('closure_reason')
        if not reason:
            return jsonify({'error': 'Closure reason is required'}), 400
        case.closure_reason = reason

    if old_status in [CaseStatus.CLOSED.value, CaseStatus.ARCHIVED.value] and new_status == CaseStatus.ACTIVE.value:
        reason = data.get('reopened_reason')
        if not reason:
            return jsonify({'error': 'Reopening reason is required'}), 400
        case.reopened_reason = reason
        case.reopened_at = datetime.now(timezone.utc)
        case.reopened_by = current_user.id
        case.closure_reason = None

    if not case.transition_status(new_status, current_user.id):
        return jsonify({
            'error': f'Cannot transition from {old_status} to {new_status}'
        }), 400

    AuditLog.log(
        user_id=current_user.id,
        action='status_change',
        entity_type='case',
        entity_id=case_id,
        changes={'status': {'old': old_status, 'new': new_status}},
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Case {case.case_number} status changed from {old_status} to {new_status}"
    )
    db.session.commit()

    return jsonify({
        'message': f'Case transitioned to {new_status}',
        'case': case.to_dict()
    })


@cms_bp.route('/cases/<case_id>/archive', methods=['POST'])
@login_required
@admin_required
def archive_case(case_id: str):
    """Archive a closed case."""
    case = db.session.get(Case, case_id) or abort(404)

    if case.status != CaseStatus.CLOSED.value:
        return jsonify({'error': 'Only closed cases can be archived'}), 400

    case.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action='archive',
        entity_type='case',
        entity_id=case_id,
        ip_address=request.remote_addr,
        description=f"Archived case: {case.case_number}"
    )
    db.session.commit()

    flash(f'Case {case.case_number} has been archived.', 'info')
    return redirect(url_for('cms.cases'))


# =============================================================================
# Subject-Case Linking Routes
# =============================================================================


@cms_bp.route('/cases/<case_id>/add-subject', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
@case_edit_required
def add_subject_to_case(case_id: str):
    """Add an existing subject to a case."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.get_json() if request.is_json else request.form

    subject_id = data.get('subject_id')
    if not subject_id:
        return jsonify({'error': 'subject_id is required'}), 400

    subject = db.session.get(Subject, subject_id) or abort(404)

    if subject in case.subjects.all():
        return jsonify({'error': 'Subject already linked to this case'}), 400

    case.subjects.append(subject)

    AuditLog.log(
        user_id=current_user.id,
        action='update',
        entity_type='case',
        entity_id=case_id,
        new_values={'added_subject': subject.name},
        ip_address=request.remote_addr,
        description=f"Added subject {subject.name} to case {case.case_number}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({'message': 'Subject added to case', 'case': case.to_dict()})

    flash(f'Subject {subject.name} added to case.', 'success')
    return redirect(url_for('cms.view_case', case_id=case_id))


@cms_bp.route('/cases/<case_id>/add-subjects-bulk', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
@case_edit_required
def bulk_add_subjects_to_case(case_id: str):
    """Add multiple subjects to a case at once."""
    case = db.session.get(Case, case_id) or abort(404)
    data = request.get_json() if request.is_json else request.form

    subject_ids = data.get('subject_ids', [])
    if not subject_ids:
        return jsonify({'error': 'subject_ids required'}), 400

    if isinstance(subject_ids, str):
        subject_ids = [s.strip() for s in subject_ids.split(',')]

    added = []
    skipped = []

    for subject_id in subject_ids:
        subject = db.session.get(Subject, subject_id)
        if not subject:
            skipped.append({'id': subject_id, 'reason': 'Not found'})
            continue

        if subject in case.subjects.all():
            skipped.append({'name': subject.name, 'reason': 'Already linked'})
            continue

        case.subjects.append(subject)
        added.append(subject.name)

        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='case',
            entity_id=case_id,
            new_values={'added_subject': subject.name},
            ip_address=request.remote_addr,
            description=f"Added subject {subject.name} to case {case.case_number}"
        )

    db.session.commit()

    result = {'added': added, 'skipped': skipped, 'total_added': len(added)}

    if request.is_json:
        return jsonify(result)

    if added:
        flash(f'Added {len(added)} subject(s) to case.', 'success')
    if skipped:
        flash(
            f'Skipped {len(skipped)} subject(s) (already linked or not found).', 'warning')

    return redirect(url_for('cms.view_case', case_id=case_id))


@cms_bp.route('/cases/<case_id>/remove-subject/<subject_id>', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
@case_edit_required
def remove_subject_from_case(case_id: str, subject_id: str):
    """Remove a subject from a case."""
    case = db.session.get(Case, case_id) or abort(404)
    subject = db.session.get(Subject, subject_id) or abort(404)

    if subject not in case.subjects.all():
        return jsonify({'error': 'Subject not linked to this case'}), 400

    case.subjects.remove(subject)

    AuditLog.log(
        user_id=current_user.id,
        action='update',
        entity_type='case',
        entity_id=case_id,
        description=f"Removed subject {subject.name} from case {case.case_number}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({'message': 'Subject removed from case'})

    flash(f'Subject {subject.name} removed from case.', 'info')
    return redirect(url_for('cms.view_case', case_id=case_id))
