"""
Case Management System - Routes
===============================
CRUD operations for all CMS entities with RBAC and audit logging.

Design Decisions:
- RESTful API design with both JSON API and HTML views
- All write operations are automatically audited
- Case-level permissions for investigators
- Soft deletes to preserve data for legal compliance
"""

import logging
import threading
import uuid
from datetime import datetime, date
from typing import Optional, Dict, Any
from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, flash, current_app
)
from flask_login import login_required, current_user

from .models import (
    db, Case, Client, Subject, Finding, FinancialRecord,
    AuditLog, Document, User, CaseStatus, CasePriority,
    SubjectType, VerificationStatus, subject_relations
)
from .auth import (
    roles_required, admin_required, senior_required,
    investigator_required, can_export, case_access_required, case_edit_required
)
from .encryption_utils import encryptor


logger = logging.getLogger(__name__)

cms_bp = Blueprint('cms', __name__, url_prefix='/cms')


# =============================================================================
# Background Search Manager
# =============================================================================

class SearchManager:
    """Manages background OSINT searches with cancellation support."""
    
    def __init__(self):
        self._searches: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def create_search(self, case_id: str, search_id: str, query: str) -> threading.Event:
        """Create a new search with cancellation event."""
        cancel_event = threading.Event()
        with self._lock:
            self._searches[search_id] = {
                'case_id': case_id,
                'query': query,
                'cancel_event': cancel_event,
                'status': 'running',
                'results': None,
                'started_at': datetime.utcnow(),
                'thread': None
            }
        return cancel_event
    
    def get_search(self, search_id: str) -> Optional[Dict[str, Any]]:
        """Get search info by ID."""
        with self._lock:
            return self._searches.get(search_id)
    
    def set_results(self, search_id: str, results: Any):
        """Set search results."""
        with self._lock:
            if search_id in self._searches:
                self._searches[search_id]['results'] = results
                self._searches[search_id]['status'] = 'completed'
                self._searches[search_id]['completed_at'] = datetime.utcnow()
    
    def cancel_search(self, search_id: str) -> bool:
        """Cancel a running search."""
        with self._lock:
            if search_id in self._searches:
                self._searches[search_id]['cancel_event'].set()
                self._searches[search_id]['status'] = 'cancelled'
                self._searches[search_id]['cancelled_at'] = datetime.utcnow()
                return True
        return False
    
    def cleanup(self, search_id: str):
        """Remove search from tracking."""
        with self._lock:
            if search_id in self._searches:
                del self._searches[search_id]
    
    def get_status(self, search_id: str) -> Optional[Dict[str, Any]]:
        """Get current search status."""
        with self._lock:
            search = self._searches.get(search_id)
            if not search:
                return None
            return {
                'status': search['status'],
                'results': search.get('results'),
                'started_at': search['started_at'].isoformat() if search.get('started_at') else None,
                'completed_at': search.get('completed_at').isoformat() if search.get('completed_at') else None,
                'cancelled_at': search.get('cancelled_at').isoformat() if search.get('cancelled_at') else None
            }


search_manager = SearchManager()


# =============================================================================
# Dashboard
# =============================================================================

@cms_bp.route('/')
@cms_bp.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard with case overview and recent activity."""
    # Get case statistics
    stats = {
        'open_cases': Case.query.filter_by(status=CaseStatus.OPEN.value, is_deleted=False).count(),
        'active_cases': Case.query.filter_by(status=CaseStatus.ACTIVE.value, is_deleted=False).count(),
        'suspended_cases': Case.query.filter_by(status=CaseStatus.SUSPENDED.value, is_deleted=False).count(),
        'closed_cases': Case.query.filter_by(status=CaseStatus.CLOSED.value, is_deleted=False).count(),
        'total_clients': Client.query.filter_by(is_deleted=False, is_active=True).count(),
        'total_subjects': Subject.query.filter_by(is_deleted=False).count()
    }
    
    # Get cases assigned to current user
    if current_user.is_admin:
        my_cases = Case.query.filter(
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
            Case.is_deleted == False
        ).order_by(Case.updated_at.desc()).limit(10).all()
    else:
        # Get case IDs from assignments table using SQLAlchemy
        from .models import case_assignments
        assigned_ids = db.session.query(case_assignments.c.case_id).filter(
            case_assignments.c.user_id == current_user.id
        ).all()
        assigned_ids = [row[0] for row in assigned_ids]
        
        my_cases = Case.query.filter(
            Case.is_deleted == False,
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
            db.or_(
                Case.assigned_to == current_user.id,
                Case.id.in_(assigned_ids) if assigned_ids else Case.id == None
            )
        ).order_by(Case.updated_at.desc()).limit(10).all()
    
    # Recent activity with user eager loaded
    recent_activity = AuditLog.query.options(
        db.joinedload(AuditLog.user)
    ).order_by(AuditLog.timestamp.desc()).limit(20).all()
    
    # Critical/high priority cases
    priority_cases = Case.query.filter(
        Case.priority.in_([CasePriority.CRITICAL.value, CasePriority.HIGH.value]),
        Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
        Case.is_deleted == False
    ).order_by(Case.start_date.asc()).limit(5).all()
    
    return render_template('cms/dashboard.html',
        stats=stats,
        my_cases=my_cases,
        recent_activity=recent_activity,
        priority_cases=priority_cases
    )


# =============================================================================
# Client Routes
# =============================================================================

@ cms_bp.route('/clients')
@ login_required
@ roles_required('admin', 'senior_investigator', 'junior_investigator')
def clients():
    """List all clients."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '')
    
    query = Client.query.filter_by(is_deleted=False)
    
    if search:
        query = query.filter(Client.name.ilike(f'%{search}%'))
    
    pagination = query.order_by(Client.name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('cms/clients/list.html',
        clients=pagination.items,
        pagination=pagination,
        search=search
    )


@cms_bp.route('/clients/<client_id>')
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def view_client(client_id: str):
    """View client details with all associated cases."""
    client = Client.query.get_or_404(client_id)
    client.decrypt_naw()  # Decrypt for display
    
    cases = Case.query.filter_by(
        client_id=client_id,
        is_deleted=False
    ).order_by(Case.created_at.desc()).all()
    
    # Log read access for sensitive data
    AuditLog.log(
        user_id=current_user.id,
        action='read',
        entity_type='client',
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Viewed client: {client.name}"
    )
    db.session.commit()
    
    return render_template('cms/clients/view.html', client=client, cases=cases)


@cms_bp.route('/clients/create', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def create_client():
    """Create a new client."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        required = ['name']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        client = Client(name=data['name'])
        
        # Set encrypted fields
        encrypted_fields = ['contact_person', 'contact_email', 'contact_phone',
                          'address_street', 'address_city', 'address_postal', 'address_country']
        for field in encrypted_fields:
            if data.get(field):
                setattr(client, field, encryptor.encrypt(data[field]))
        
        # Set other fields
        client.contract_number = data.get('contract_number')
        client.contract_info = data.get('contract_info')
        
        db.session.add(client)
        
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='client',
            entity_id=client.id,
            new_values={'name': client.name},
            ip_address=request.remote_addr,
            description=f"Created client: {client.name}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'Client created', 'client': client.to_dict()}), 201
        
        flash(f'Client {client.name} created successfully.', 'success')
        return redirect(url_for('cms.view_client', client_id=client.id))
    
    return render_template('cms/clients/create.html')


@cms_bp.route('/clients/<client_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def edit_client(client_id: str):
    """Edit client details."""
    client = Client.query.get_or_404(client_id)
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        changes = {}
        
        # Update name
        if data.get('name') and data['name'] != client.name:
            changes['name'] = {'old': client.name, 'new': data['name']}
            client.name = data['name']
        
        # Update encrypted fields
        encrypted_fields = ['contact_person', 'contact_email', 'contact_phone',
                          'address_street', 'address_city', 'address_postal', 'address_country']
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(client, field)
                if new_value != old_value:
                    changes[field] = {'old': '[encrypted]', 'new': '[encrypted]'}
                    if new_value:
                        setattr(client, field, encryptor.encrypt(new_value))
                    else:
                        setattr(client, field, None)
        
        # Update contract info
        if data.get('contract_number') != client.contract_number:
            changes['contract_number'] = {'old': client.contract_number, 'new': data.get('contract_number')}
            client.contract_number = data.get('contract_number')
        
        client.updated_at = datetime.utcnow()
        
        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='client',
            entity_id=client_id,
            changes=changes,
            ip_address=request.remote_addr,
            description=f"Updated client: {client.name}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'Client updated', 'client': client.to_dict()})
        
        flash('Client updated successfully.', 'success')
        return redirect(url_for('cms.view_client', client_id=client.id))
    
    client.decrypt_naw()
    return render_template('cms/clients/edit.html', client=client)


@cms_bp.route('/clients/<client_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_client(client_id: str):
    """Soft delete a client."""
    client = Client.query.get_or_404(client_id)
    
    client.soft_delete()
    
    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='client',
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Deleted client: {client.name}"
    )
    db.session.commit()
    
    flash(f'Client {client.name} has been archived.', 'info')
    
    if request.is_json:
        return jsonify({'message': 'Client archived'})
    return redirect(url_for('cms.clients'))


# =============================================================================
# Case Routes
# =============================================================================

@cms_bp.route('/cases')
@login_required
def cases():
    """List all cases with filtering and search."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    status = request.args.get('status', '')
    priority = request.args.get('priority', '')
    search = request.args.get('search', '')
    client_filter = request.args.get('client', '')
    assigned = request.args.get('assigned', '')
    
    query = Case.query.filter_by(is_deleted=False).join(Client)
    
    # Filter by status
    if status:
        query = query.filter_by(status=status)
    
    # Filter by priority
    if priority:
        query = query.filter_by(priority=priority)
    
    # Filter by client
    if client_filter:
        query = query.filter(Client.id == client_filter)
    
    # Search in title, case number, description
    if search:
        query = query.filter(
            db.or_(
                Case.title.ilike(f'%{search}%'),
                Case.case_number.ilike(f'%{search}%'),
                Case.description.ilike(f'%{search}%'),
                Client.name.ilike(f'%{search}%')
            )
        )
    
    # Filter by assignment (non-admins see only assigned cases)
    if assigned == 'me' and not current_user.is_admin:
        from .models import case_assignments
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
    
    pagination = query.order_by(Case.updated_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Get clients for dropdown
    clients = Client.query.filter_by(is_deleted=False, is_active=True).all()
    
    return render_template('cms/cases/list.html',
        cases=pagination.items,
        pagination=pagination,
        clients=clients,
        filters={'status': status, 'priority': priority, 'search': search, 'client': client_filter, 'assigned': assigned}
    )


@cms_bp.route('/cases/<case_id>')
@login_required
@case_access_required
def view_case(case_id: str):
    """View case details with subjects, findings, and financials."""
    case = Case.query.get_or_404(case_id)
    subjects = case.subjects.all()
    
    findings_page = request.args.get('findings_page', 1, type=int)
    findings_per_page = 20
    findings_pagination = case.findings.filter_by(is_deleted=False).order_by(
        Finding.created_at.desc()
    ).paginate(page=findings_page, per_page=findings_per_page, error_out=False)
    
    financials = case.financial_records.filter_by(is_deleted=False).order_by(FinancialRecord.transaction_date.desc()).all()
    
    # Get documents
    documents = Document.query.filter_by(case_id=case_id, is_deleted=False).order_by(Document.created_at.desc()).all()
    
    # Get all available subjects (not already linked to this case)
    linked_ids = [s.id for s in subjects]
    all_subjects = Subject.query.filter(Subject.is_deleted == False).all()
    available_subjects = [s for s in all_subjects if s.id not in linked_ids]
    
    # Log read access
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
        all_subjects=available_subjects
    )


@cms_bp.route('/cases/<case_id>/timeline')
@login_required
@case_access_required
def case_timeline(case_id: str):
    """Get timeline of all events for a case."""
    case = Case.query.get_or_404(case_id)
    
    timeline = []
    
    # Case created
    timeline.append({
        'timestamp': case.created_at,
        'type': 'create',
        'icon': '📁',
        'title': 'Case Created',
        'description': f'{case.case_number} - {case.title}',
        'user': None,
        'details': f'Client: {case.client.name if case.client else "N/A"}'
    })
    
    # Status transitions from audit log
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
    
    # Subjects added
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
    
    # Findings added
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
    
    # OSINT searches
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
    
    # Financial records added
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
    
    # Reopen events
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
    
    # Close event
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
    
    # Sort by timestamp descending (newest first)
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
        
        # Validate client exists
        client = Client.query.get(data['client_id'])
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
            created_by=current_user.id
        )
        
        db.session.add(case)
        db.session.flush()  # Get case ID
        
        # Assign to user if specified
        if data.get('assigned_to'):
            case.assigned_to = data['assigned_to']
        
        # Add subjects if provided
        if data.get('subject_ids'):
            for subject_id in data['subject_ids']:
                subject = Subject.query.get(subject_id)
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
    case = Case.query.get_or_404(case_id)
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        changes = {}
        
        editable_fields = ['title', 'description', 'priority',
                         'case_type', 'jurisdiction', 'tags']
        
        for field in editable_fields:
            if field in data:
                old_value = getattr(case, field)
                new_value = data[field]
                if new_value != old_value:
                    changes[field] = {'old': str(old_value) if old_value else None, 'new': str(new_value)}
                    setattr(case, field, new_value)
        
        # Handle target_end_date separately (needs date conversion)
        if 'target_end_date' in data and data['target_end_date']:
            try:
                from datetime import datetime
                new_date = datetime.strptime(data['target_end_date'], '%Y-%m-%d').date()
                old_date = case.target_end_date
                if new_date != old_date:
                    changes['target_end_date'] = {'old': str(old_date) if old_date else None, 'new': str(new_date)}
                    case.target_end_date = new_date
            except ValueError:
                pass
        
        # Status transition
        if data.get('status') and data['status'] != case.status:
            if case.transition_status(data['status'], current_user.id):
                changes['status'] = {'old': case.status, 'new': data['status']}
            else:
                return jsonify({'error': 'Invalid status transition'}), 400
        
        case.updated_at = datetime.utcnow()
        
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
    case = Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form
    
    new_status = data.get('status')
    if not new_status:
        return jsonify({'error': 'Status is required'}), 400
    
    old_status = case.status
    
    # Handle closing with reason
    if new_status == CaseStatus.CLOSED.value:
        reason = data.get('closure_reason')
        if not reason:
            return jsonify({'error': 'Closure reason is required'}), 400
        case.closure_reason = reason
    
    # Handle reopening from closed/archived
    if old_status in [CaseStatus.CLOSED.value, CaseStatus.ARCHIVED.value] and new_status == CaseStatus.ACTIVE.value:
        reason = data.get('reopened_reason')
        if not reason:
            return jsonify({'error': 'Reopening reason is required'}), 400
        case.reopened_reason = reason
        case.reopened_at = datetime.utcnow()
        case.reopened_by = current_user.id
        case.closure_reason = None  # Clear previous closure reason
    
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
    case = Case.query.get_or_404(case_id)
    
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
# Subject Routes
# =============================================================================

@cms_bp.route('/subjects')
@login_required
def subjects():
    """List all subjects with search and filtering."""
    page = request.args.get('page', 1, type=int)
    per_page = 30
    search = request.args.get('search', '')
    subject_type = request.args.get('type', '')
    fmt = request.args.get('format', '')
    
    query = Subject.query.filter_by(is_deleted=False)
    
    if search:
        query = query.filter(Subject.name.ilike(f'%{search}%'))
    
    if subject_type:
        query = query.filter_by(subject_type=subject_type)
    
    # JSON format for API calls
    if fmt == 'json':
        subjects_list = query.order_by(Subject.name).all()
        return jsonify({
            'subjects': [{'id': s.id, 'name': s.name, 'type': s.subject_type} for s in subjects_list]
        })
    
    pagination = query.order_by(Subject.name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('cms/subjects/list.html',
        subjects=pagination.items,
        pagination=pagination,
        filters={'search': search, 'type': subject_type}
    )


@cms_bp.route('/subjects/<subject_id>')
@login_required
def view_subject(subject_id: str):
    """View subject details."""
    subject = Subject.query.get_or_404(subject_id)
    subject.decrypt_identifiers()
    
    financials = subject.financial_records.filter_by(is_deleted=False).all()
    findings = subject.findings.filter_by(is_deleted=False).order_by(Finding.created_at.desc()).all()
    
    # Get linked cases
    linked_cases = []
    for case in Case.query.all():
        if subject in case.subjects.all():
            linked_cases.append({'id': case.id, 'case_number': case.case_number, 'title': case.title})
    
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
        financials=financials,
        findings=findings,
        linked_cases=linked_cases
    )


@cms_bp.route('/subjects/create', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def create_subject():
    """Create a new subject."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        required = ['name', 'subject_type']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        subject = Subject(
            name=data['name'],
            subject_type=data['subject_type'],
            risk_score=data.get('risk_score', 0),
            risk_factors=data.get('risk_factors'),
            notes=data.get('notes'),
            registration_number=data.get('registration_number'),
            legal_form=data.get('legal_form'),
            asset_type=data.get('asset_type'),
            estimated_value=data.get('estimated_value'),
            currency=data.get('currency', 'EUR')
        )
        
        # Encrypt identifying information
        encrypted_fields = ['date_of_birth', 'place_of_birth', 'nationality',
                          'identification_number', 'address', 'phone', 'email']
        for field in encrypted_fields:
            if data.get(field):
                setattr(subject, field, encryptor.encrypt(data[field]))
        
        db.session.add(subject)
        
        # Link to case if specified
        if data.get('case_id'):
            case = Case.query.get(data['case_id'])
            if case:
                case.subjects.append(subject)
        
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='subject',
            entity_id=subject.id,
            new_values={'name': subject.name, 'type': subject.subject_type},
            ip_address=request.remote_addr,
            case_id=data.get('case_id'),
            description=f"Created subject: {subject.name}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'Subject created', 'subject': subject.to_dict()}), 201
        
        flash(f'Subject {subject.name} created successfully.', 'success')
        
        # If created from case view, redirect back to case
        if data.get('case_id'):
            return redirect(url_for('cms.view_case', case_id=data['case_id']))
        
        return redirect(url_for('cms.view_subject', subject_id=subject.id))
    
    # Pass case_id from query param if coming from case view
    case_id = request.args.get('case_id')
    return render_template('cms/subjects/create.html', case_id=case_id)


@cms_bp.route('/subjects/<subject_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def edit_subject(subject_id: str):
    """Edit subject details."""
    subject = Subject.query.get_or_404(subject_id)
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        changes = {}
        
        # Update basic fields
        if data.get('name') and data['name'] != subject.name:
            changes['name'] = {'old': subject.name, 'new': data['name']}
            subject.name = data['name']
        
        if 'risk_score' in data:
            changes['risk_score'] = {'old': subject.risk_score, 'new': data['risk_score']}
            subject.risk_score = int(data['risk_score'])
        
        if 'notes' in data:
            subject.notes = data['notes']
        
        # Update encrypted fields
        encrypted_fields = ['date_of_birth', 'place_of_birth', 'nationality',
                          'identification_number', 'address', 'phone', 'email']
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                if new_value != getattr(subject, field):
                    changes[field] = {'old': '[encrypted]', 'new': '[encrypted]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)
        
        subject.updated_at = datetime.utcnow()
        
        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='subject',
            entity_id=subject_id,
            changes=changes,
            ip_address=request.remote_addr,
            description=f"Updated subject: {subject.name}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'Subject updated', 'subject': subject.to_dict()})
        
        flash('Subject updated successfully.', 'success')
        return redirect(url_for('cms.view_subject', subject_id=subject.id))
    
    subject.decrypt_identifiers()
    return render_template('cms/subjects/edit.html', subject=subject)


@cms_bp.route('/subjects/<subject_id>/photo', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def upload_subject_photo(subject_id: str):
    """Upload a photo for a subject."""
    subject = Subject.query.get_or_404(subject_id)
    
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo provided'}), 400
    
    file = request.files['photo']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Only allow images
    allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({'error': 'Only image files allowed'}), 400
    
    # Create upload directory
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'subjects', subject_id)
    os.makedirs(upload_dir, exist_ok=True)
    
    # Remove old photo if exists
    if subject.photo_path:
        old_path = os.path.join(current_app.root_path, 'static', subject.photo_path.lstrip('/'))
        if os.path.exists(old_path):
            os.remove(old_path)
    
    # Save new photo
    filename = f"photo.{ext}"
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)
    
    # Update subject
    subject.photo_path = f"/uploads/subjects/{subject_id}/{filename}"
    
    AuditLog.log(
        user_id=current_user.id,
        action='update',
        entity_type='subject',
        entity_id=subject_id,
        changes={'photo': 'uploaded'},
        ip_address=request.remote_addr,
        description=f"Uploaded photo for {subject.name}"
    )
    db.session.commit()
    
    return jsonify({
        'message': 'Photo uploaded',
        'photo_path': subject.photo_path
    })


# =============================================================================
# Subject Relationship Routes
# =============================================================================

@cms_bp.route('/subjects/<subject_id>/relationships')
@login_required
def get_subject_relationships(subject_id: str):
    """Get relationship network data for a subject."""
    try:
        subject = Subject.query.get_or_404(subject_id)
        
        # Get direct relationships via direct query
        related_rows = db.session.execute(
            subject_relations.select().where(subject_relations.c.subject_id == subject.id)
        ).fetchall()
        
        related_ids = [row.related_subject_id for row in related_rows]
        related = Subject.query.filter(Subject.id.in_(related_ids), Subject.is_deleted == False).all() if related_ids else []
        
        # Build nodes and edges for visualization
        nodes = [{
            'id': subject.id,
            'name': subject.name,
            'type': subject.subject_type,
            'isMain': True
        }]
        
        edges = []
        edge_ids = set()
        
        for rel in related:
            nodes.append({
                'id': rel.id,
                'name': rel.name,
                'type': rel.subject_type,
                'isMain': False
            })
            
            # Get relationship type from the association table
            rel_type = 'related'
            type_rows = db.session.execute(
                subject_relations.select().where(
                    (subject_relations.c.subject_id == subject.id) & 
                    (subject_relations.c.related_subject_id == rel.id)
                )
            ).fetchall()
            if type_rows:
                rel_type = type_rows[0].relationship_type or 'related'
            
            edge_id = f"{subject.id}-{rel.id}"
            if edge_id not in edge_ids:
                edges.append({
                    'id': edge_id,
                    'source': subject.id,
                    'target': rel.id,
                    'type': rel_type
                })
                edge_ids.add(edge_id)
        
        # Get second-degree connections (friends of friends)
        for rel in related:
            second_degree_rows = db.session.execute(
                subject_relations.select().where(subject_relations.c.subject_id == rel.id)
            ).fetchall()
            
            second_degree_ids = [row.related_subject_id for row in second_degree_rows if row.related_subject_id != subject.id]
            rel_related = Subject.query.filter(
                Subject.id.in_(second_degree_ids),
                Subject.is_deleted == False,
                Subject.id != subject.id
            ).all() if second_degree_ids else []
            
            for rr in rel_related:
                # Check if node already exists
                if not any(n['id'] == rr.id for n in nodes):
                    nodes.append({
                        'id': rr.id,
                        'name': rr.name,
                        'type': rr.subject_type,
                        'isMain': False
                    })
                
                edge_id = f"{rel.id}-{rr.id}"
                rev_edge_id = f"{rr.id}-{rel.id}"
                if edge_id not in edge_ids and rev_edge_id not in edge_ids:
                    rel_type = 'connected'
                    type_rows = db.session.execute(
                        subject_relations.select().where(
                            (subject_relations.c.subject_id == rel.id) & 
                            (subject_relations.c.related_subject_id == rr.id)
                        )
                    ).fetchall()
                    if type_rows:
                        rel_type = type_rows[0].relationship_type or 'connected'
                    
                    edges.append({
                        'id': edge_id,
                        'source': rel.id,
                        'target': rr.id,
                        'type': rel_type
                    })
                    edge_ids.add(edge_id)
        
        return jsonify({
            'subject': {
                'id': subject.id,
                'name': subject.name,
                'type': subject.subject_type
            },
            'nodes': nodes,
            'edges': edges
        })
    except Exception as e:
        logger.error(f"Error in get_subject_relationships: {str(e)}")
        return jsonify({'error': str(e), 'error_type': type(e).__name__}), 500


@cms_bp.route('/subjects/<subject_id>/add-relationship', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def add_subject_relationship(subject_id: str):
    """Add a relationship between two subjects."""
    subject = Subject.query.get_or_404(subject_id)
    data = request.get_json()
    
    related_id = data.get('related_subject_id')
    relationship_type = data.get('relationship_type', 'related')
    
    if not related_id:
        return jsonify({'error': 'Related subject ID required'}), 400
    
    if related_id == subject_id:
        return jsonify({'error': 'Cannot create relationship with self'}), 400
    
    related = Subject.query.get(related_id)
    if not related:
        return jsonify({'error': 'Related subject not found'}), 404
    
    # Check if relationship already exists
    existing = db.session.execute(
        subject_relations.select().where(
            (subject_relations.c.subject_id == subject.id) & 
            (subject_relations.c.related_subject_id == related_id)
        )
    ).first()
    
    if existing:
        return jsonify({'error': 'Relationship already exists'}), 400
    
    # Add relationship (single direction - bidirectional is handled by querying)
    db.session.execute(
        subject_relations.insert().values(
            subject_id=subject.id,
            related_subject_id=related_id,
            relationship_type=relationship_type
        )
    )
    
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='subject_relation',
        entity_id=f"{subject.id}-{related_id}",
        ip_address=request.remote_addr,
        description=f"Added {relationship_type} relationship between {subject.name} and {related.name}"
    )
    db.session.commit()
    
    return jsonify({
        'message': 'Relationship added',
        'relationship': {
            'subject_id': subject.id,
            'related_subject_id': related_id,
            'type': relationship_type
        }
    })


@cms_bp.route('/subjects/<subject_id>/remove-relationship', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def remove_subject_relationship(subject_id: str):
    """Remove a relationship between two subjects."""
    subject = Subject.query.get_or_404(subject_id)
    data = request.get_json()
    
    related_id = data.get('related_subject_id')
    
    if not related_id:
        return jsonify({'error': 'Related subject ID required'}), 400
    
    # Remove both directions
    db.session.execute(
        subject_relations.delete().where(
            ((subject_relations.c.subject_id == subject.id) & 
             (subject_relations.c.related_subject_id == related_id)) |
            ((subject_relations.c.subject_id == related_id) & 
             (subject_relations.c.related_subject_id == subject.id))
        )
    )
    
    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='subject_relation',
        entity_id=f"{subject.id}-{related_id}",
        ip_address=request.remote_addr,
        description=f"Removed relationship between {subject.name} and subject {related_id}"
    )
    db.session.commit()
    
    return jsonify({'message': 'Relationship removed'})


# =============================================================================
# Subject-Case Linking Routes
# =============================================================================

@cms_bp.route('/cases/<case_id>/add-subject', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
@case_edit_required
def add_subject_to_case(case_id: str):
    """Add an existing subject to a case."""
    case = Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form
    
    subject_id = data.get('subject_id')
    if not subject_id:
        return jsonify({'error': 'subject_id is required'}), 400
    
    subject = Subject.query.get_or_404(subject_id)
    
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


@cms_bp.route('/cases/<case_id>/remove-subject/<subject_id>', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
@case_edit_required
def remove_subject_from_case(case_id: str, subject_id: str):
    """Remove a subject from a case."""
    case = Case.query.get_or_404(case_id)
    subject = Subject.query.get_or_404(subject_id)
    
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


# =============================================================================
# Financial Record Routes
# =============================================================================

@cms_bp.route('/financials/create', methods=['POST'])
@login_required
@senior_required
def create_financial():
    """Create a new financial record."""
    data = request.get_json() if request.is_json else request.form
    
    required = ['case_id', 'transaction_date', 'amount']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    
    record = FinancialRecord(
        case_id=data['case_id'],
        subject_id=data.get('subject_id'),
        transaction_date=data['transaction_date'],
        amount=data['amount'],
        currency=data.get('currency', 'EUR'),
        transaction_type=data.get('transaction_type'),
        source=data.get('source'),
        source_reference=data.get('source_reference'),
        description=data.get('description')
    )
    
    # Encrypt counterparty details
    encrypted_fields = ['counterparty_name', 'counterparty_account', 
                       'counterparty_bank', 'counterparty_country']
    for field in encrypted_fields:
        if data.get(field):
            setattr(record, field, encryptor.encrypt(data[field]))
    
    db.session.add(record)
    
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='financial_record',
        entity_id=record.id,
        ip_address=request.remote_addr,
        case_id=data['case_id'],
        new_values={'amount': str(record.amount), 'date': str(record.transaction_date)},
        description=f"Added financial record: {record.amount} {record.currency}"
    )
    db.session.commit()
    
    if request.is_json:
        return jsonify({'message': 'Financial record created', 'record': record.to_dict()}), 201
    
    flash('Financial record added.', 'success')
    return redirect(url_for('cms.view_case', case_id=data['case_id']))


@cms_bp.route('/financials/<record_id>/verify', methods=['POST'])
@login_required
@senior_required
def verify_financial(record_id: str):
    """Verify or flag a financial record."""
    record = FinancialRecord.query.get_or_404(record_id)
    data = request.get_json()
    
    action = data.get('action')  # 'verify' or 'flag'
    notes = data.get('notes', '')
    
    if action == 'verify':
        record.verify(current_user.id, notes)
        action_type = 'verify'
    else:
        record.flag(current_user.id, notes)
        action_type = 'flag'
    
    AuditLog.log(
        user_id=current_user.id,
        action=action_type,
        entity_type='financial_record',
        entity_id=record_id,
        ip_address=request.remote_addr,
        case_id=record.case_id,
        description=f"{action_type.capitalize()}d financial record"
    )
    db.session.commit()
    
    return jsonify({'message': f'Record {action_type}ed', 'record': record.to_dict()})


# =============================================================================
# Finding Routes
# =============================================================================

@cms_bp.route('/findings/create', methods=['POST'])
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


# =============================================================================
# Search Routes
# =============================================================================

@cms_bp.route('/search')
@login_required
def search():
    """Global search across all entities."""
    query = request.args.get('q', '')
    entity_type = request.args.get('type', '')  # case, client, subject
    
    if not query or len(query) < 2:
        return jsonify({'results': []})
    
    results = {'cases': [], 'clients': [], 'subjects': []}
    
    if not entity_type or entity_type == 'case':
        cases = Case.query.filter(
            Case.is_deleted == False,
            db.or_(
                Case.title.ilike(f'%{query}%'),
                Case.case_number.ilike(f'%{query}%'),
                Case.description.ilike(f'%{query}%')
            )
        ).limit(10).all()
        results['cases'] = [c.to_dict(include_relations=False) for c in cases]
    
    if not entity_type or entity_type == 'client':
        clients = Client.query.filter(
            Client.is_deleted == False,
            Client.name.ilike(f'%{query}%')
        ).limit(10).all()
        results['clients'] = [c.to_dict() for c in clients]
    
    if not entity_type or entity_type == 'subject':
        subjects = Subject.query.filter(
            Subject.is_deleted == False,
            Subject.name.ilike(f'%{query}%')
        ).limit(10).all()
        results['subjects'] = [s.to_dict(decrypted=False) for s in subjects]
    
    # Log search for audit
    AuditLog.log(
        user_id=current_user.id,
        action='search',
        entity_type='global_search',
        ip_address=request.remote_addr,
        description=f"Searched for: {query}"
    )
    db.session.commit()
    
    return jsonify({'results': results, 'query': query})


# =============================================================================
# Audit Log Routes
# =============================================================================

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
    
    query = AuditLog.query
    
    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    if action:
        query = query.filter_by(action=action)
    if user_id:
        query = query.filter_by(user_id=user_id)
    if case_id:
        query = query.filter_by(case_id=case_id)
    
    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('cms/audit/log.html',
        logs=pagination.items,
        pagination=pagination,
        filters={'entity_type': entity_type, 'action': action, 'user_id': user_id, 'case_id': case_id}
    )


# =============================================================================
# OSINT Background Search Routes
# =============================================================================

def run_osint_search(search_id: str, case_id: str, query: str, name: str):
    """Run OSINT search in background thread."""
    from app import search_person
    
    search_info = search_manager.get_search(search_id)
    if not search_info:
        return
    
    cancel_event = search_info['cancel_event']
    results = None
    
    try:
        logger.info(f"OSINT search {search_id} started for query: {name}")
        
        # Run the search
        results = search_person(name)
        
        # Check if cancelled before setting results
        if cancel_event.is_set():
            logger.info(f"OSINT search {search_id} was cancelled")
            search_manager.cleanup(search_id)
            return
        
        # Set results
        search_manager.set_results(search_id, results)
        logger.info(f"OSINT search {search_id} completed with {len(results.get('search_links', []))} results")
        
        # Cleanup after a delay (give client time to fetch results)
        def delayed_cleanup():
            import time
            time.sleep(300)  # Keep results for 5 minutes
            search_manager.cleanup(search_id)
        
        cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
        cleanup_thread.start()
        
    except Exception as e:
        logger.error(f"OSINT search {search_id} failed: {str(e)}")
        search_manager.cleanup(search_id)


@cms_bp.route('/cases/<case_id>/osint-search', methods=['POST'])
@login_required
@case_access_required
def start_osint_search(case_id: str):
    """Start a background OSINT search for a person."""
    case = Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form
    
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    if len(name.split()) < 2:
        return jsonify({'error': 'Please enter a full name (first and last name)'}), 400
    
    # Create search
    search_id = str(uuid.uuid4())
    cancel_event = search_manager.create_search(case_id, search_id, name)
    
    # Log the search start
    AuditLog.log(
        user_id=current_user.id,
        action='osint_search_start',
        entity_type='case',
        entity_id=case_id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Started OSINT search for: {name}"
    )
    db.session.commit()
    
    # Start background thread
    thread = threading.Thread(
        target=run_osint_search,
        args=(search_id, case_id, name, name),
        daemon=True
    )
    thread.start()
    
    # Update search info with thread reference
    with search_manager._lock:
        if search_id in search_manager._searches:
            search_manager._searches[search_id]['thread'] = thread
    
    return jsonify({
        'search_id': search_id,
        'status': 'started',
        'message': f'Search started for: {name}'
    })


@cms_bp.route('/osint-search/<search_id>/status')
@login_required
def get_search_status(search_id: str):
    """Get the status of a background search."""
    status = search_manager.get_status(search_id)
    
    if not status:
        return jsonify({'error': 'Search not found'}), 404
    
    return jsonify({
        'search_id': search_id,
        **status
    })


@cms_bp.route('/osint-search/<search_id>/cancel', methods=['POST'])
@login_required
def cancel_search(search_id: str):
    """Cancel a running search."""
    search_info = search_manager.get_search(search_id)
    
    if not search_info:
        return jsonify({'error': 'Search not found'}), 404
    
    if search_info['status'] not in ['running']:
        return jsonify({'error': 'Search is not running'}), 400
    
    search_manager.cancel_search(search_id)
    
    # Log cancellation
    AuditLog.log(
        user_id=current_user.id,
        action='osint_search_cancel',
        entity_type='osint_search',
        entity_id=search_id,
        ip_address=request.remote_addr,
        case_id=search_info.get('case_id'),
        description=f"Cancelled OSINT search for: {search_info.get('query')}"
    )
    db.session.commit()
    
    # Cleanup
    search_manager.cleanup(search_id)
    
    return jsonify({
        'search_id': search_id,
        'status': 'cancelled',
        'message': 'Search cancelled'
    })


@cms_bp.route('/osint-search/<search_id>/results')
@login_required
def get_search_results(search_id: str):
    """Get results from a completed search."""
    status = search_manager.get_status(search_id)
    
    if not status:
        return jsonify({'error': 'Search not found'}), 404
    
    if status['status'] == 'running':
        return jsonify({
            'search_id': search_id,
            'status': 'running',
            'results': None
        })
    
    return jsonify({
        'search_id': search_id,
        'status': status['status'],
        'results': status.get('results'),
        'completed_at': status.get('completed_at')
    })


@cms_bp.route('/cases/<case_id>/osint-search/add-findings', methods=['POST'])
@login_required
@case_access_required
def add_osint_findings(case_id: str):
    """Add selected OSINT results as findings to a case."""
    case = Case.query.get_or_404(case_id)
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    selected_results = data.get('results', [])
    if not selected_results:
        return jsonify({'error': 'No results selected'}), 400
    
    subject_id = data.get('subject_id')
    created_findings = []
    
    for result in selected_results:
        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"OSINT: {result.get('engine', 'Unknown')} - {result.get('name', 'Search Result')}",
            content=result.get('query', '') + f"\n\nSource: {result.get('url', 'N/A')}",
            source_url=result.get('url', ''),
            source_type='osint',
            finding_type='identity',
            reliability_score=5,
            confidence_level='medium',
            created_by=current_user.id,
            tags=['osint', result.get('engine', '').lower()]
        )
        
        db.session.add(finding)
        created_findings.append(finding)
    
    # Log the action
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='finding',
        entity_id=None,
        ip_address=request.remote_addr,
        case_id=case_id,
        new_values={'count': len(created_findings), 'source': 'osint_search'},
        description=f"Added {len(created_findings)} OSINT findings to case {case.case_number}"
    )
    db.session.commit()
    
    return jsonify({
        'message': f'{len(created_findings)} findings added',
        'findings': [f.to_dict() for f in created_findings]
    }), 201


# =============================================================================
# Document Upload Routes
# =============================================================================

import os
import uuid
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'csv'}
UPLOAD_FOLDER = 'uploads'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@cms_bp.route('/cases/<case_id>/upload', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def upload_case_document(case_id: str):
    """Upload a document to a case."""
    case = Case.query.get_or_404(case_id)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    # Create upload directory if not exists
    upload_dir = os.path.join(current_app.root_path, 'static', UPLOAD_FOLDER, 'cases', case_id)
    os.makedirs(upload_dir, exist_ok=True)
    
    # Generate unique filename
    original_filename = secure_filename(file.filename)
    file_ext = original_filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
    file_path = os.path.join(upload_dir, unique_filename)
    
    # Save file
    file.save(file_path)
    
    # Get file size
    file_size = os.path.getsize(file_path)
    
    # Create document record
    document = Document(
        case_id=case_id,
        filename=unique_filename,
        original_filename=original_filename,
        mime_type=file.content_type,
        file_size=file_size,
        storage_path=f"{UPLOAD_FOLDER}/cases/{case_id}/{unique_filename}",
        storage_type='local',
        document_type=request.form.get('document_type', 'evidence'),
        description=request.form.get('description', ''),
        classification=request.form.get('classification', 'confidential'),
        uploaded_by=current_user.id
    )
    
    db.session.add(document)
    
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='document',
        entity_id=document.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Uploaded document: {original_filename}"
    )
    db.session.commit()
    
    return jsonify({
        'message': 'Document uploaded',
        'document': document.to_dict()
    }), 201


@cms_bp.route('/subjects/<subject_id>/upload', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def upload_subject_document(subject_id: str):
    """Upload a document to a subject."""
    subject = Subject.query.get_or_404(subject_id)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    # Create upload directory
    upload_dir = os.path.join(current_app.root_path, 'static', UPLOAD_FOLDER, 'subjects', subject_id)
    os.makedirs(upload_dir, exist_ok=True)
    
    # Generate unique filename
    original_filename = secure_filename(file.filename)
    file_ext = original_filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
    file_path = os.path.join(upload_dir, unique_filename)
    
    file.save(file_path)
    file_size = os.path.getsize(file_path)
    
    document = Document(
        subject_id=subject_id,
        filename=unique_filename,
        original_filename=original_filename,
        mime_type=file.content_type,
        file_size=file_size,
        storage_path=f"{UPLOAD_FOLDER}/subjects/{subject_id}/{unique_filename}",
        storage_type='local',
        document_type=request.form.get('document_type', 'evidence'),
        description=request.form.get('description', ''),
        classification=request.form.get('classification', 'confidential'),
        uploaded_by=current_user.id
    )
    
    db.session.add(document)
    
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='document',
        entity_id=document.id,
        ip_address=request.remote_addr,
        description=f"Uploaded document to {subject.name}: {original_filename}"
    )
    db.session.commit()
    
    return jsonify({
        'message': 'Document uploaded',
        'document': document.to_dict()
    }), 201


@cms_bp.route('/documents/<document_id>')
@login_required
def get_document(document_id: str):
    """Get document metadata."""
    document = Document.query.get_or_404(document_id)
    
    # Check access
    if document.case_id:
        case = Case.query.get(document.case_id)
        if case and not current_user.can_access_case(case):
            return jsonify({'error': 'Access denied'}), 403
    
    return jsonify(document.to_dict())


@cms_bp.route('/documents/<document_id>/download')
@login_required
def download_document(document_id: str):
    """Download a document."""
    document = Document.query.get_or_404(document_id)
    
    # Check access
    if document.case_id:
        case = Case.query.get(document.case_id)
        if case and not current_user.can_access_case(case):
            return jsonify({'error': 'Access denied'}), 403
    
    from flask import send_from_directory, abort
    import os
    
    file_path = os.path.join(current_app.root_path, 'static', document.storage_path)
    
    if not os.path.exists(file_path):
        abort(404)
    
    return send_from_directory(
        os.path.dirname(file_path),
        os.path.basename(file_path),
        as_attachment=True,
        download_name=document.original_filename
    )


@cms_bp.route('/documents/<document_id>', methods=['DELETE'])
@login_required
@roles_required('admin', 'senior_investigator')
def delete_document(document_id: str):
    """Delete a document."""
    document = Document.query.get_or_404(document_id)
    
    # Delete file
    file_path = os.path.join(current_app.root_path, 'static', document.storage_path)
    if os.path.exists(file_path):
        os.remove(file_path)
    
    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='document',
        entity_id=document_id,
        ip_address=request.remote_addr,
        description=f"Deleted document: {document.original_filename}"
    )
    
    db.session.delete(document)
    db.session.commit()
    
    return jsonify({'message': 'Document deleted'})


@cms_bp.route('/cases/<case_id>/documents')
@login_required
@case_access_required
def get_case_documents(case_id: str):
    """Get all documents for a case."""
    documents = Document.query.filter_by(case_id=case_id, is_deleted=False).order_by(
        Document.created_at.desc()
    ).all()
    
    return jsonify({
        'documents': [d.to_dict() for d in documents]
    })


# =============================================================================
# Financial Summary Routes
# =============================================================================

@cms_bp.route('/cases/<case_id>/financial-summary')
@login_required
@case_access_required
def get_financial_summary(case_id: str):
    """Get aggregated financial data for a case."""
    case = Case.query.get_or_404(case_id)
    records = FinancialRecord.query.filter_by(case_id=case_id, is_deleted=False).all()
    
    if not records:
        return jsonify({
            'summary': {
                'total_records': 0,
                'total_amount': 0,
                'currency': 'EUR',
                'by_type': {},
                'by_status': {},
                'by_source': {},
                'by_month': {},
                'top_counterparties': []
            }
        })
    
    # Calculate totals
    total_amount = sum(float(r.amount or 0) for r in records)
    
    # Group by transaction type
    by_type = {}
    for r in records:
        t = r.transaction_type or 'unknown'
        if t not in by_type:
            by_type[t] = {'count': 0, 'total': 0}
        by_type[t]['count'] += 1
        by_type[t]['total'] += float(r.amount or 0)
    
    # Group by verification status
    by_status = {}
    for r in records:
        s = r.verification_status or 'pending'
        if s not in by_status:
            by_status[s] = {'count': 0, 'total': 0}
        by_status[s]['count'] += 1
        by_status[s]['total'] += float(r.amount or 0)
    
    # Group by source
    by_source = {}
    for r in records:
        s = r.source or 'unknown'
        if s not in by_source:
            by_source[s] = {'count': 0, 'total': 0}
        by_source[s]['count'] += 1
        by_source[s]['total'] += float(r.amount or 0)
    
    # Group by month
    by_month = {}
    for r in records:
        if r.transaction_date:
            month_key = r.transaction_date.strftime('%Y-%m')
            if month_key not in by_month:
                by_month[month_key] = {'count': 0, 'total': 0}
            by_month[month_key]['count'] += 1
            by_month[month_key]['total'] += float(r.amount or 0)
    
    # Top counterparties
    counterparties = {}
    for r in records:
        name = r.counterparty_name or 'Unknown'
        if name not in counterparties:
            counterparties[name] = 0
        counterparties[name] += float(r.amount or 0)
    
    top_counterparties = sorted(
        [{'name': k, 'total': v} for k, v in counterparties.items()],
        key=lambda x: x['total'],
        reverse=True
    )[:10]
    
    return jsonify({
        'summary': {
            'total_records': len(records),
            'total_amount': round(total_amount, 2),
            'currency': records[0].currency if records else 'EUR',
            'by_type': by_type,
            'by_status': by_status,
            'by_source': by_source,
            'by_month': by_month,
            'top_counterparties': top_counterparties
        }
    })
