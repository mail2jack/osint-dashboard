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
    redirect, url_for, flash, current_app, send_file
)
from flask_login import login_required, current_user

from .models import (
    db, Case, Client, Subject, Finding, FinancialRecord,
    AuditLog, Document, User, CaseStatus, CasePriority,
    SubjectType, VerificationStatus, subject_relations, Comment,
    CommentEditHistory, DocumentTemplate, Reminder, ReminderType, ReminderRecurrence,
    Screenshot
)
from .auth import (
    roles_required, admin_required, senior_required,
    investigator_required, can_export, case_access_required, case_edit_required
)
from .encryption_utils import encryptor


logger = logging.getLogger(__name__)

cms_bp = Blueprint('cms', __name__, url_prefix='/cms')


# =============================================================================
# Duplicate Detection Utility
# =============================================================================

def normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    import re
    if not name:
        return ""
    # Lowercase, remove extra spaces, remove common prefixes/suffixes
    normalized = name.lower().strip()
    normalized = re.sub(r'\s+', ' ', normalized)  # Multiple spaces to single
    # Remove common prefixes
    for prefix in ['mr.', 'mrs.', 'ms.', 'dr.', 'ing.', 'ir.']:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    return normalized

def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate similarity between two strings (0-1)."""
    s1, s2 = normalize_name(s1), normalize_name(s2)
    if not s1 or not s2:
        return 0.0
    
    # Exact match
    if s1 == s2:
        return 1.0
    
    # Quick checks
    if s1 in s2 or s2 in s1:
        return 0.85
    
    # Levenshtein-based similarity
    len_sum = len(s1) + len(s2)
    if len_sum == 0:
        return 0.0
    
    # Simple character-based similarity
    common = sum(1 for a, b in zip(s1, s2) if a == b)
    return common * 2 / len_sum

def find_similar_subjects(name: str, threshold: float = 0.7) -> list:
    """Find subjects with similar names."""
    if not name or len(name) < 2:
        return []
    
    normalized_input = normalize_name(name)
    similar = []
    
    for subject in Subject.query.filter_by(is_deleted=False).all():
        if normalize_name(subject.name) == normalized_input:
            continue  # Skip exact matches (handled separately)
        
        similarity = calculate_similarity(name, subject.name)
        if similarity >= threshold:
            similar.append({
                'id': subject.id,
                'name': subject.name,
                'type': subject.subject_type,
                'similarity': round(similarity * 100)
            })
    
    return sorted(similar, key=lambda x: x['similarity'], reverse=True)

def find_similar_clients(name: str, threshold: float = 0.7) -> list:
    """Find clients with similar names."""
    if not name or len(name) < 2:
        return []
    
    normalized_input = normalize_name(name)
    similar = []
    
    for client in Client.query.filter_by(is_deleted=False, is_active=True).all():
        if normalize_name(client.name) == normalized_input:
            continue
        
        similarity = calculate_similarity(name, client.name)
        if similarity >= threshold:
            similar.append({
                'id': client.id,
                'name': client.name,
                'similarity': round(similarity * 100)
            })
    
    return sorted(similar, key=lambda x: x['similarity'], reverse=True)

def check_for_exact_match(name: str, entity_type: str) -> Optional[dict]:
    """Check for exact or very close match."""
    normalized = normalize_name(name)
    
    if entity_type == 'subject':
        for subject in Subject.query.filter_by(is_deleted=False).all():
            if normalize_name(subject.name) == normalized:
                return {
                    'id': subject.id,
                    'name': subject.name,
                    'type': subject.subject_type,
                    'exact': True
                }
    elif entity_type == 'client':
        for client in Client.query.filter_by(is_deleted=False, is_active=True).all():
            if normalize_name(client.name) == normalized:
                return {
                    'id': client.id,
                    'name': client.name,
                    'exact': True
                }
    
    return None


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
        'archived_cases': Case.query.filter_by(status=CaseStatus.ARCHIVED.value, is_deleted=False).count(),
        'total_clients': Client.query.filter_by(is_deleted=False, is_active=True).count(),
        'total_subjects': Subject.query.filter_by(is_deleted=False).count(),
        'total_findings': Finding.query.filter_by(is_deleted=False).count(),
        'high_risk_subjects': Subject.query.filter(Subject.risk_score >= 70, Subject.is_deleted == False).count()
    }
    
    # Chart data - cases by status
    status_labels = ['Open', 'Active', 'Suspended', 'Closed', 'Archived']
    status_values = [
        stats['open_cases'],
        stats['active_cases'],
        stats['suspended_cases'],
        stats['closed_cases'],
        stats['archived_cases']
    ]
    
    # Chart data - cases by priority
    priority_data = {
        'critical': Case.query.filter_by(priority=CasePriority.CRITICAL.value, is_deleted=False).count(),
        'high': Case.query.filter_by(priority=CasePriority.HIGH.value, is_deleted=False).count(),
        'medium': Case.query.filter_by(priority=CasePriority.MEDIUM.value, is_deleted=False).count(),
        'low': Case.query.filter_by(priority=CasePriority.LOW.value, is_deleted=False).count()
    }
    
    # Recent cases (last 30 days)
    from datetime import timedelta
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_cases = Case.query.filter(
        Case.created_at >= thirty_days_ago,
        Case.is_deleted == False
    ).count()
    
    # Subject type distribution
    subject_types = db.session.query(
        Subject.subject_type,
        db.func.count(Subject.id)
    ).filter(Subject.is_deleted == False).group_by(Subject.subject_type).all()
    subject_type_labels = [s[0] for s in subject_types]
    subject_type_values = [s[1] for s in subject_types]
    
    # Cases by criminal code type (extract Niv3 code from case_type field)
    from sqlalchemy import func, case as sql_case
    case_type_stats = db.session.query(
        func.substr(Case.case_type, 1, func.instr(Case.case_type, '|') - 1).label('code'),
        func.count(Case.id).label('count')
    ).filter(
        Case.is_deleted == False,
        Case.case_type.isnot(None),
        Case.case_type != '',
        Case.case_type.like('%|%')  # Only show cases with criminal code format
    ).group_by(
        func.substr(Case.case_type, 1, func.instr(Case.case_type, '|') - 1)
    ).order_by(func.count(Case.id).desc()).limit(10).all()
    
    case_type_labels = [s.code if s.code else 'Unknown' for s in case_type_stats]
    case_type_values = [s.count for s in case_type_stats]
    
    # Lead investigator workload
    lead_investigator_stats = db.session.query(
        User.full_name,
        func.count(Case.id).label('case_count')
    ).join(
        Case, Case.lead_investigator_id == User.id
    ).filter(
        Case.is_deleted == False,
        Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value])
    ).group_by(User.id, User.full_name).order_by(func.count(Case.id).desc()).all()
    
    investigator_names = [s.full_name for s in lead_investigator_stats]
    investigator_counts = [s.case_count for s in lead_investigator_stats]
    
    # My cases stats for quick filters (include case_assignments table)
    from .models import case_assignments
    my_assigned_ids = db.session.query(case_assignments.c.case_id).filter(
        case_assignments.c.user_id == current_user.id
    ).all()
    my_assigned_ids = [row[0] for row in my_assigned_ids]
    
    my_open_cases = Case.query.filter(
        Case.is_deleted == False,
        Case.status == CaseStatus.OPEN.value,
        db.or_(
            Case.assigned_to == current_user.id,
            Case.lead_investigator_id == current_user.id,
            Case.id.in_(my_assigned_ids) if my_assigned_ids else False
        )
    ).count()
    
    my_active_cases = Case.query.filter(
        Case.is_deleted == False,
        Case.status == CaseStatus.ACTIVE.value,
        db.or_(
            Case.assigned_to == current_user.id,
            Case.lead_investigator_id == current_user.id,
            Case.id.in_(my_assigned_ids) if my_assigned_ids else False
        )
    ).count()
    
    overdue_cases = Case.query.filter(
        Case.is_deleted == False,
        Case.target_end_date < datetime.utcnow().date(),
        Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value])
    ).count()
    
    # Get cases assigned to current user (always show user's own cases, even for admins)
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
            Case.lead_investigator_id == current_user.id,
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
    
    # Get upcoming reminders for current user
    now = datetime.utcnow()
    overdue_reminders = Reminder.query.filter(
        Reminder.is_deleted == False,
        Reminder.is_completed == False,
        Reminder.reminder_date < now
    ).order_by(Reminder.reminder_date.asc()).limit(10).all()
    
    upcoming_reminders = Reminder.query.filter(
        Reminder.is_deleted == False,
        Reminder.is_completed == False,
        Reminder.reminder_date >= now
    ).order_by(Reminder.reminder_date.asc()).limit(5).all()
    
    # Update overdue status
    for r in overdue_reminders:
        r.is_overdue = True
    if overdue_reminders:
        db.session.commit()
    
    return render_template('cms/dashboard.html',
        stats=stats,
        my_cases=my_cases,
        recent_activity=recent_activity,
        priority_cases=priority_cases,
        status_labels=status_labels,
        status_values=status_values,
        priority_data=priority_data,
        recent_cases=recent_cases,
        subject_type_labels=subject_type_labels,
        subject_type_values=subject_type_values,
        overdue_reminders=overdue_reminders,
        upcoming_reminders=upcoming_reminders,
        case_type_labels=case_type_labels,
        case_type_values=case_type_values,
        investigator_names=investigator_names,
        investigator_counts=investigator_counts,
        my_open_cases=my_open_cases,
        my_active_cases=my_active_cases,
        overdue_cases=overdue_cases
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
    sort = request.args.get('sort', 'name')
    order = request.args.get('order', 'asc')
    
    query = Client.query.filter_by(is_deleted=False)
    
    if search:
        query = query.filter(Client.name.ilike(f'%{search}%'))
    
    # Sorting
    sort_columns = {
        'name': Client.name,
        'contact': Client.contact_person,
        'contract': Client.contract_number,
    }
    
    sort_col = sort_columns.get(sort, Client.name)
    if order == 'desc':
        sort_col = sort_col.desc()
    
    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('cms/clients/list.html',
        clients=pagination.items,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order
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
    """Create a new client with duplicate detection."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        required = ['name']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        name = data['name'].strip()
        
        # Check for duplicates
        exact_match = check_for_exact_match(name, 'client')
        similar = find_similar_clients(name)
        
        # Skip duplicate check if already confirmed
        if not data.get('confirm_duplicate'):
            if exact_match:
                if request.is_json:
                    return jsonify({
                        'error': 'exact_match',
                        'message': f'A client with this name already exists: {exact_match["name"]}',
                        'duplicate': exact_match,
                        'similar': similar[:5]
                    }), 409
                flash(f'Warning: A client with this name already exists: {exact_match["name"]}', 'warning')
                return render_template('cms/clients/create.html',
                    duplicate_warning=True,
                    exact_match=exact_match,
                    similar_clients=similar[:5],
                    submitted_name=name,
                    submitted_is_company=bool(data.get('is_company')))
            
            if similar and not request.is_json:
                flash(f'Warning: Similar clients found. Please review before creating.', 'warning')
                return render_template('cms/clients/create.html',
                    duplicate_warning=True,
                    similar_clients=similar[:5],
                    submitted_name=name,
                    submitted_is_company=bool(data.get('is_company')))
        
        client = Client(name=name)
        client.is_company = bool(data.get('is_company'))
        
        # Set encrypted fields
        encrypted_fields = ['contact_person', 'contact_email', 'contact_phone',
                          'address_street', 'address_city', 'address_postal', 'address_country',
                          'social_security_number', 'bank_account']
        for field in encrypted_fields:
            if data.get(field):
                setattr(client, field, encryptor.encrypt(data[field]))
        
        # Set other fields
        client.contract_number = data.get('contract_number')
        client.contract_info = data.get('contract_info')
        client.vat_number = data.get('vat_number')
        client.financial_notes = data.get('financial_notes')
        
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
        
        # Update is_company
        new_is_company = bool(data.get('is_company'))
        if new_is_company != client.is_company:
            changes['is_company'] = {'old': client.is_company, 'new': new_is_company}
            client.is_company = new_is_company
        
        # Update encrypted fields
        encrypted_fields = ['contact_person', 'contact_email', 'contact_phone',
                          'address_street', 'address_city', 'address_postal', 'address_country',
                          'social_security_number', 'bank_account']
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
        
        # Update financial fields
        if data.get('vat_number') != client.vat_number:
            client.vat_number = data.get('vat_number')
        if data.get('financial_notes') != client.financial_notes:
            client.financial_notes = data.get('financial_notes')
        
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


@cms_bp.route('/clients/<client_id>/archive', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def archive_client(client_id: str):
    """Archive a client if no cases exist."""
    client = Client.query.get_or_404(client_id)
    
    # Check if client has any cases
    active_cases = Case.query.filter_by(client_id=client_id, is_deleted=False).count()
    if active_cases > 0:
        return jsonify({'error': f'Cannot archive: client has {active_cases} active case(s)'}), 400
    
    client.is_deleted = True
    client.deleted_at = datetime.utcnow()
    
    AuditLog.log(
        user_id=current_user.id,
        action='archive',
        entity_type='client',
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Archived client: {client.name}"
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
    
    # Filter by status - hide closed cases by default unless explicitly selected
    if status:
        query = query.filter(Case.status == status)
    else:
        query = query.filter(Case.status != CaseStatus.CLOSED.value)
    
    # Filter by priority
    if priority:
        query = query.filter(Case.priority == priority)
    
    # Filter by case type (stored as "code|beleid|beleidcode")
    case_type_filter = request.args.get('case_type', '')
    if case_type_filter:
        query = query.filter(Case.case_type.like(f'{case_type_filter}|%'))
    
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
    
    # Sorting
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
    
    # Get clients for dropdown
    clients = Client.query.filter_by(is_deleted=False, is_active=True).all()
    
    return render_template('cms/cases/list.html',
        cases=pagination.items,
        pagination=pagination,
        clients=clients,
        filters={'status': status, 'priority': priority, 'search': search, 'client': client_filter, 'assigned': assigned, 'sort': sort, 'order': order, 'case_type': case_type_filter}
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
    
    # Get reminders for this case
    case_reminders = Reminder.query.filter_by(
        case_id=case_id,
        is_deleted=False
    ).order_by(Reminder.reminder_date.asc()).all()
    
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
        all_subjects=available_subjects,
        case_reminders=case_reminders
    )


@cms_bp.route('/cases/<case_id>/set-parent', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
@case_edit_required
def set_case_parent(case_id: str):
    """Set the parent case for a case."""
    case = Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form
    
    parent_id = data.get('parent_case_id')
    
    if parent_id:
        # Validate parent exists
        parent = Case.query.get(parent_id)
        if not parent or parent.is_deleted:
            return jsonify({'error': 'Parent case not found'}), 404
        
        # Prevent circular references
        if parent_id == case_id:
            return jsonify({'error': 'A case cannot be its own parent'}), 400
        
        # Check for circular reference (parent cannot have this case as ancestor)
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
        # Remove parent
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
    case = Case.query.get_or_404(case_id)
    return jsonify({
        'parent': {
            'id': case.parent_case.id,
            'case_number': case.parent_case.case_number,
            'title': case.parent_case.title
        } if case.parent_case else None,
        'children': [
            {'id': c.id, 'case_number': c.case_number, 'title': c.title, 'status': c.status}
            for c in case.child_cases.filter_by(is_deleted=False)
        ]
    })


@cms_bp.route('/api/cases/<case_id>/audit-log')
@login_required
def get_case_audit_log_api(case_id: str):
    """Get audit log for a case via API."""
    case = Case.query.get_or_404(case_id)
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
            created_by=current_user.id,
            lead_investigator_id=data.get('lead_investigator_id') or None
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
                         'case_type', 'jurisdiction']
        
        for field in editable_fields:
            if field in data:
                old_value = getattr(case, field)
                new_value = data[field]
                if new_value != old_value:
                    changes[field] = {'old': str(old_value) if old_value else None, 'new': str(new_value)}
                    setattr(case, field, new_value)
        
        # Handle tags (comma-separated string to list)
        if 'tags' in data:
            tags_value = data['tags']
            if isinstance(tags_value, str):
                new_tags = [t.strip() for t in tags_value.split(',') if t.strip()]
            elif isinstance(tags_value, list):
                new_tags = tags_value
            else:
                new_tags = []
            
            old_tags = case.tags or []
            if sorted(new_tags) != sorted(old_tags):
                changes['tags'] = {'old': old_tags, 'new': new_tags}
                case.tags = new_tags if new_tags else None
        
        # Handle lead_investigator_id
        new_lead = data.get('lead_investigator_id') or None
        if new_lead != case.lead_investigator_id:
            changes['lead_investigator_id'] = {'old': case.lead_investigator_id, 'new': new_lead}
            case.lead_investigator_id = new_lead
        
        # Handle target_end_date separately (needs date conversion)
        if 'target_end_date' in data and data['target_end_date']:
            try:
                from datetime import datetime as dt
                new_date = dt.strptime(data['target_end_date'], '%Y-%m-%d').date()
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
        query = query.filter(Subject.name.ilike(f'%{search}%'))
    
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
    
    # JSON format for API calls
    if fmt == 'json':
        subjects_list = query.order_by(sort_col).all()
        return jsonify({
            'subjects': [{'id': s.id, 'name': s.name, 'type': s.subject_type} for s in subjects_list]
        })
    
    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('cms/subjects/list.html',
        subjects=pagination.items,
        pagination=pagination,
        filters={'search': search, 'type': subject_type, 'sort': sort, 'order': order}
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
    first_case_id = None
    for case in Case.query.filter_by(is_deleted=False).all():
        if subject in case.subjects.all():
            case_info = {'id': case.id, 'case_number': case.case_number, 'title': case.title}
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
        financials=financials,
        findings=findings,
        linked_cases=linked_cases,
        first_case_id=first_case_id
    )


@cms_bp.route('/api/check-duplicate')
@login_required
def check_duplicate():
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


# =============================================================================
# Export Routes
# =============================================================================

import csv
import io
from flask import Response


@cms_bp.route('/cases/<case_id>/export')
@login_required
@case_access_required
def export_case(case_id: str):
    """Export case data as CSV."""
    case = Case.query.get_or_404(case_id)
    format_type = request.args.get('format', 'csv')
    
    if format_type == 'csv':
        return export_case_csv(case)
    else:
        return jsonify({'error': 'Unsupported format. Use csv.'}), 400


def export_case_csv(case: Case) -> Response:
    """Generate CSV export of case data."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Case info
    writer.writerow(['Case Report'])
    writer.writerow(['Case Number', case.case_number])
    writer.writerow(['Title', case.title])
    writer.writerow(['Client', case.client.name if case.client else 'N/A'])
    writer.writerow(['Status', case.status])
    writer.writerow(['Priority', case.priority])
    writer.writerow(['Start Date', case.start_date])
    writer.writerow(['Target End Date', case.target_end_date or 'N/A'])
    writer.writerow(['Actual End Date', case.actual_end_date or 'N/A'])
    writer.writerow(['Description', case.description or 'N/A'])
    writer.writerow(['Case Type', case.case_type or 'N/A'])
    writer.writerow(['Jurisdiction', case.jurisdiction or 'N/A'])
    writer.writerow([])
    
    # Subjects
    writer.writerow(['Subjects'])
    writer.writerow(['Name', 'Type', 'Risk Score', 'Email', 'Phone', 'Address'])
    for subject in case.subjects.all():
        subject.decrypt_identifiers()
        writer.writerow([
            subject.name,
            subject.subject_type,
            subject.risk_score,
            subject.email or 'N/A',
            subject.phone or 'N/A',
            subject.address or 'N/A'
        ])
    writer.writerow([])
    
    # Findings
    writer.writerow(['Findings'])
    writer.writerow(['Title', 'Type', 'Severity', 'Status', 'Created', 'Description'])
    for finding in case.findings.filter_by(is_deleted=False).all():
        writer.writerow([
            finding.title,
            finding.finding_type,
            finding.severity,
            finding.status,
            finding.created_at.strftime('%Y-%m-%d %H:%M'),
            (finding.description or '')[:200]
        ])
    writer.writerow([])
    
    # Financial Records
    writer.writerow(['Financial Records'])
    writer.writerow(['Date', 'Amount', 'Type', 'Counterparty', 'Description'])
    for record in case.financial_records.filter_by(is_deleted=False).all():
        writer.writerow([
            record.transaction_date.strftime('%Y-%m-%d'),
            record.amount,
            record.transaction_type,
            record.counterparty_name or 'N/A',
            (record.description or '')[:200]
        ])
    
    output.seek(0)
    filename = f"case_{case.case_number.replace('-', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@cms_bp.route('/subjects/export')
@login_required
@can_export
def export_subjects():
    """Export all subjects as CSV."""
    format_type = request.args.get('format', 'csv')
    
    if format_type == 'csv':
        return export_subjects_csv()
    else:
        return jsonify({'error': 'Unsupported format. Use csv.'}), 400


def export_subjects_csv() -> Response:
    """Generate CSV export of all subjects."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Name', 'Type', 'Risk Score', 'Email', 'Phone', 'Address', 'Notes', 'Created'])
    
    for subject in Subject.query.filter_by(is_deleted=False).order_by(Subject.name).all():
        subject.decrypt_identifiers()
        writer.writerow([
            subject.name,
            subject.subject_type,
            subject.risk_score,
            subject.email or '',
            subject.phone or '',
            subject.address or '',
            (subject.notes or '')[:200],
            subject.created_at.strftime('%Y-%m-%d')
        ])
    
    output.seek(0)
    filename = f"subjects_export_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@cms_bp.route('/clients/export')
@login_required
@can_export
def export_clients():
    """Export all clients as CSV."""
    format_type = request.args.get('format', 'csv')
    
    if format_type == 'csv':
        return export_clients_csv()
    else:
        return jsonify({'error': 'Unsupported format. Use csv.'}), 400


def export_clients_csv() -> Response:
    """Generate CSV export of all clients."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Name', 'Type', 'Contact Person', 'Email', 'Phone', 'Contract Number', 'Active', 'Created'])
    
    for client in Client.query.filter_by(is_deleted=False).order_by(Client.name).all():
        client.decrypt_naw()
        writer.writerow([
            client.name,
            'Company' if client.is_company else 'Individual',
            client.contact_person or '',
            client.contact_email or '',
            client.contact_phone or '',
            client.contract_number or '',
            'Yes' if client.is_active else 'No',
            client.created_at.strftime('%Y-%m-%d')
        ])
    
    output.seek(0)
    filename = f"clients_export_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.get_value(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@cms_bp.route('/cases/export')
@login_required
@can_export
def export_cases():
    """Export all cases as CSV."""
    format_type = request.args.get('format', 'csv')
    
    if format_type == 'csv':
        return export_cases_csv()
    else:
        return jsonify({'error': 'Unsupported format. Use csv.'}), 400


def export_cases_csv() -> Response:
    """Generate CSV export of all cases."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Case Number', 'Title', 'Client', 'Status', 'Priority', 'Start Date', 'End Date', 'Type', 'Subjects Count', 'Findings Count'])
    
    for case in Case.query.filter_by(is_deleted=False).order_by(Case.case_number).all():
        writer.writerow([
            case.case_number,
            case.title,
            case.client.name if case.client else 'N/A',
            case.status,
            case.priority,
            case.start_date.strftime('%Y-%m-%d'),
            case.actual_end_date.strftime('%Y-%m-%d') if case.actual_end_date else '',
            case.case_type or '',
            case.subjects.count(),
            case.findings.filter_by(is_deleted=False).count()
        ])
    
    output.seek(0)
    filename = f"cases_export_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@cms_bp.route('/subjects/create', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def create_subject():
    """Create a new subject with duplicate detection."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        required = ['name', 'subject_type']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        name = data['name'].strip()
        
        # Check for duplicates
        exact_match = check_for_exact_match(name, 'subject')
        similar = find_similar_subjects(name)
        
        # Skip duplicate check if already confirmed
        if not data.get('confirm_duplicate'):
            if exact_match:
                if request.is_json:
                    return jsonify({
                        'error': 'exact_match',
                        'message': f'A subject with this name already exists: {exact_match["name"]}',
                        'duplicate': exact_match,
                        'similar': similar[:5]
                    }), 409
                flash(f'Warning: A subject with this name already exists: {exact_match["name"]}', 'warning')
                case_id = request.args.get('case_id')
                return render_template('cms/subjects/create.html', 
                    case_id=case_id,
                    duplicate_warning=True,
                    exact_match=exact_match,
                    similar_subjects=similar[:5],
                    submitted_name=name,
                    submitted_type=data.get('subject_type'))
            
            if similar and not request.is_json:
                flash(f'Warning: Similar subjects found. Please review before creating.', 'warning')
                case_id = request.args.get('case_id')
                return render_template('cms/subjects/create.html',
                    case_id=case_id,
                    duplicate_warning=True,
                    similar_subjects=similar[:5],
                    submitted_name=name,
                    submitted_type=data.get('subject_type'))
        
        subject = Subject(
            name=name,
            subject_type=data['subject_type'],
            risk_score=data.get('risk_score', 0),
            risk_factors=data.get('risk_factors'),
            notes=data.get('notes'),
            registration_number=data.get('registration_number'),
            legal_form=data.get('legal_form'),
            asset_type=data.get('asset_type'),
            estimated_value=data.get('estimated_value'),
            currency=data.get('currency', 'EUR'),
            license_plate=data.get('license_plate'),
            vin=data.get('vin'),
            insurance_company=data.get('insurance_company'),
            brand=data.get('brand'),
            vehicle_type=data.get('vehicle_type')
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
        
        # Update vehicle fields
        vehicle_fields = ['license_plate', 'vin', 'insurance_company', 'brand', 'vehicle_type']
        for field in vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                if new_value != getattr(subject, field):
                    changes[field] = {'old': getattr(subject, field), 'new': new_value}
                    setattr(subject, field, new_value)
        
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
        
        # Get ALL relationships for this subject (both directions now)
        related_rows = db.session.execute(
            subject_relations.select().where(subject_relations.c.subject_id == subject.id)
        ).fetchall()
        
        # Build a map of related subjects
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
        edge_ids = set()  # Use sorted IDs to avoid duplicates
        
        # Helper to get sorted edge ID
        def sorted_edge_id(a, b):
            return f"{min(a, b)}-{max(a, b)}"
        
        for rel in related:
            nodes.append({
                'id': rel.id,
                'name': rel.name,
                'type': rel.subject_type,
                'isMain': False
            })
            
            # Get relationship type from either direction
            rel_type = 'related'
            type_rows = db.session.execute(
                subject_relations.select().where(
                    (subject_relations.c.subject_id == subject.id) & 
                    (subject_relations.c.related_subject_id == rel.id)
                )
            ).fetchall()
            if not type_rows:
                # Check reverse direction
                type_rows = db.session.execute(
                    subject_relations.select().where(
                        (subject_relations.c.subject_id == rel.id) & 
                        (subject_relations.c.related_subject_id == subject.id)
                    )
                ).fetchall()
            if type_rows:
                rel_type = type_rows[0].relationship_type or 'related'
            
            edge_id = sorted_edge_id(subject.id, rel.id)
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
                
                edge_id = sorted_edge_id(rel.id, rr.id)
                if edge_id not in edge_ids:
                    rel_type = 'connected'
                    type_rows = db.session.execute(
                        subject_relations.select().where(
                            (subject_relations.c.subject_id == rel.id) & 
                            (subject_relations.c.related_subject_id == rr.id)
                        )
                    ).fetchall()
                    if not type_rows:
                        # Check reverse direction
                        type_rows = db.session.execute(
                            subject_relations.select().where(
                                (subject_relations.c.subject_id == rr.id) & 
                                (subject_relations.c.related_subject_id == rel.id)
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
    """Add a bidirectional relationship between two subjects."""
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
    
    # Check if relationship already exists in EITHER direction
    existing_a = db.session.execute(
        subject_relations.select().where(
            (subject_relations.c.subject_id == subject.id) & 
            (subject_relations.c.related_subject_id == related_id)
        )
    ).first()
    
    existing_b = db.session.execute(
        subject_relations.select().where(
            (subject_relations.c.subject_id == related_id) & 
            (subject_relations.c.related_subject_id == subject.id)
        )
    ).first()
    
    if existing_a or existing_b:
        return jsonify({'error': 'Relationship already exists'}), 400
    
    # Add relationship in BOTH directions
    db.session.execute(
        subject_relations.insert().values(
            subject_id=subject.id,
            related_subject_id=related_id,
            relationship_type=relationship_type
        )
    )
    db.session.execute(
        subject_relations.insert().values(
            subject_id=related_id,
            related_subject_id=subject.id,
            relationship_type=relationship_type
        )
    )
    
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='subject_relation',
        entity_id=f"{subject.id}-{related_id}",
        ip_address=request.remote_addr,
        description=f"Added bidirectional {relationship_type} relationship between {subject.name} and {related.name}"
    )
    db.session.commit()
    
    return jsonify({
        'message': 'Relationship added',
        'relationship': {
            'subject_id': subject.id,
            'related_subject_id': related_id,
            'type': relationship_type,
            'bidirectional': True
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


@cms_bp.route('/cases/<case_id>/add-subjects-bulk', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
@case_edit_required
def bulk_add_subjects_to_case(case_id: str):
    """Add multiple subjects to a case at once."""
    case = Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form
    
    subject_ids = data.get('subject_ids', [])
    if not subject_ids:
        return jsonify({'error': 'subject_ids required'}), 400
    
    if isinstance(subject_ids, str):
        subject_ids = [s.strip() for s in subject_ids.split(',')]
    
    added = []
    skipped = []
    
    for subject_id in subject_ids:
        subject = Subject.query.get(subject_id)
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
        flash(f'Skipped {len(skipped)} subject(s) (already linked or not found).', 'warning')
    
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
        filters={'entity_type': entity_type, 'action': action, 'user_id': user_id, 'case_id': case_id, 'search': search},
        users=users,
        entity_types=entity_types,
        actions=actions
    )


# =============================================================================
# Comment Routes
# =============================================================================

@cms_bp.route('/api/comments', methods=['POST'])
@login_required
def create_comment():
    """Create a new comment on any entity."""
    data = request.get_json()
    
    if not data.get('content'):
        return jsonify({'error': 'Content is required'}), 400
    
    # At least one entity must be specified
    entity_ids = {
        'case_id': data.get('case_id'),
        'subject_id': data.get('subject_id'),
        'client_id': data.get('client_id'),
        'financial_record_id': data.get('financial_record_id')
    }
    
    if not any(entity_ids.values()):
        return jsonify({'error': 'At least one entity ID is required'}), 400
    
    comment = Comment(
        content=data['content'],
        comment_type=data.get('comment_type', 'note'),
        is_pinned=bool(data.get('is_pinned', False)),
        author_id=current_user.id,
        **entity_ids
    )
    
    db.session.add(comment)
    
    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='comment',
        entity_id=comment.id,
        ip_address=request.remote_addr,
        case_id=data.get('case_id'),
        description=f"Added comment on {data.get('case_id') and 'case' or data.get('subject_id') and 'subject' or data.get('client_id') and 'client' or 'entity'}"
    )
    db.session.commit()
    
    return jsonify(comment.to_dict()), 201


@cms_bp.route('/api/comments/<comment_id>', methods=['PUT'])
@login_required
def update_comment(comment_id: str):
    """Update a comment."""
    comment = Comment.query.get_or_404(comment_id)
    
    # Only author or admin can edit
    if comment.author_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Not authorized to edit this comment'}), 403
    
    data = request.get_json()
    content_changed = False
    
    if 'content' in data and data['content'] != comment.content:
        CommentEditHistory(
            comment_id=comment.id,
            previous_content=comment.content,
            new_content=data['content'],
            edited_by_id=current_user.id,
            edited_at=datetime.utcnow()
        )
        comment.content = data['content']
        comment.edit_count = (comment.edit_count or 0) + 1
        comment.last_edited_by_id = current_user.id
        comment.last_edited_at = datetime.utcnow()
        content_changed = True
    
    if 'is_pinned' in data:
        comment.is_pinned = data['is_pinned']
    
    if 'is_resolved' in data:
        comment.is_resolved = data['is_resolved']
    
    comment.updated_at = datetime.utcnow()
    db.session.commit()
    
    if content_changed:
        AuditLog.log(
            user_id=current_user.id,
            action='comment_edit',
            entity_type='comment',
            entity_id=comment.id,
            ip_address=request.remote_addr,
            case_id=comment.case_id,
            description=f"Edited comment (edit #{comment.edit_count})"
        )
        db.session.commit()
    
    return jsonify(comment.to_dict())


@cms_bp.route('/api/comments/<comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id: str):
    """Delete a comment."""
    comment = Comment.query.get_or_404(comment_id)
    
    # Only author or admin can delete
    if comment.author_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Not authorized to delete this comment'}), 403
    
    comment.soft_delete()
    
    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='comment',
        entity_id=comment_id,
        ip_address=request.remote_addr,
        description=f"Deleted comment"
    )
    db.session.commit()
    
    return jsonify({'message': 'Comment deleted'})


@cms_bp.route('/api/comments/for-entity')
@login_required
def get_comments_for_entity():
    """Get all comments for a specific entity."""
    entity_type = request.args.get('type')  # case, subject, client, financial_record
    entity_id = request.args.get('id')
    
    query = Comment.query.filter_by(is_deleted=False)
    
    if entity_type == 'case' and entity_id:
        query = query.filter_by(case_id=entity_id)
    elif entity_type == 'subject' and entity_id:
        query = query.filter_by(subject_id=entity_id)
    elif entity_type == 'client' and entity_id:
        query = query.filter_by(client_id=entity_id)
    elif entity_type == 'financial_record' and entity_id:
        query = query.filter_by(financial_record_id=entity_id)
    else:
        return jsonify({'error': 'Invalid entity type or missing ID'}), 400
    
    comments = query.order_by(Comment.is_pinned.desc(), Comment.created_at.desc()).all()
    
    return jsonify({
        'comments': [c.to_dict() for c in comments],
        'count': len(comments)
    })


@cms_bp.route('/api/comments/count')
@login_required
def get_comment_count():
    """Get comment count for a specific entity."""
    entity_type = request.args.get('type')
    entity_id = request.args.get('id')
    
    query = Comment.query.filter_by(is_deleted=False)
    
    if entity_type == 'case' and entity_id:
        count = query.filter_by(case_id=entity_id).count()
    elif entity_type == 'subject' and entity_id:
        count = query.filter_by(subject_id=entity_id).count()
    elif entity_type == 'client' and entity_id:
        count = query.filter_by(client_id=entity_id).count()
    else:
        count = 0
    
    return jsonify({'count': count})


# =============================================================================
# Document Template Routes
# =============================================================================

from jinja2 import Template as JinjaTemplate
from jinja2 import Environment, BaseLoader


@cms_bp.route('/templates')
@login_required
def list_templates():
    """List all document templates."""
    templates = DocumentTemplate.query.filter_by(is_active=True).order_by(DocumentTemplate.name).all()
    return render_template('cms/templates/list.html', templates=templates)


@cms_bp.route('/templates/create', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def create_template():
    """Create a new document template."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        template = DocumentTemplate(
            name=data['name'],
            description=data.get('description'),
            template_type=data.get('template_type', 'report'),
            content=data['content'],
            category=data.get('category'),
            is_default=bool(data.get('is_default')),
            created_by=current_user.id
        )
        
        db.session.add(template)
        
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='document_template',
            entity_id=template.id,
            ip_address=request.remote_addr,
            description=f"Created document template: {template.name}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify(template.to_dict()), 201
        
        flash(f'Template "{template.name}" created.', 'success')
        return redirect(url_for('cms.list_templates'))
    
    return render_template('cms/templates/create.html')


@cms_bp.route('/templates/<template_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def edit_template(template_id: str):
    """Edit a document template."""
    template = DocumentTemplate.query.get_or_404(template_id)
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        template.name = data['name']
        template.description = data.get('description')
        template.template_type = data.get('template_type', 'report')
        template.content = data['content']
        template.category = data.get('category')
        template.is_default = bool(data.get('is_default'))
        template.updated_at = datetime.utcnow()
        
        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='document_template',
            entity_id=template.id,
            ip_address=request.remote_addr,
            description=f"Updated document template: {template.name}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify(template.to_dict())
        
        flash(f'Template "{template.name}" updated.', 'success')
        return redirect(url_for('cms.list_templates'))
    
    return render_template('cms/templates/edit.html', template=template)


@cms_bp.route('/templates/<template_id>/delete', methods=['POST'])
@login_required
@roles_required('admin')
def delete_template(template_id: str):
    """Delete a document template."""
    template = DocumentTemplate.query.get_or_404(template_id)
    
    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='document_template',
        entity_id=template.id,
        ip_address=request.remote_addr,
        description=f"Deleted document template: {template.name}"
    )
    
    db.session.delete(template)
    db.session.commit()
    
    flash(f'Template deleted.', 'success')
    return redirect(url_for('cms.list_templates'))


@cms_bp.route('/templates/<template_id>/preview')
@login_required
def preview_template(template_id: str):
    """Preview a template with sample data."""
    template = DocumentTemplate.query.get_or_404(template_id)
    
    # Build sample context
    context = _build_report_context(None)
    rendered = template.render(context)
    
    return jsonify({'rendered': rendered})


@cms_bp.route('/cases/<case_id>/generate-report', methods=['GET', 'POST'])
@login_required
@case_access_required
def generate_case_report(case_id: str):
    """Generate a report from a template for a specific case."""
    case = Case.query.get_or_404(case_id)
    
    templates = DocumentTemplate.query.filter_by(is_active=True).order_by(DocumentTemplate.name).all()
    
    if request.method == 'POST':
        template_id = request.form.get('template_id')
        custom_fields = {
            'conclusion': request.form.get('conclusion', ''),
            'recommendation': request.form.get('recommendation', ''),
            'classification': request.form.get('classification', 'Confidential')
        }
        
        template = DocumentTemplate.query.get_or_404(template_id)
        
        # Build context from case
        context = _build_report_context(case)
        context.update(custom_fields)
        context['user'] = current_user
        
        rendered = template.render(context)
        
        # Save as document
        doc = Document(
            case_id=case.id,
            filename=f"report_{case.case_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            original_filename=f"{template.name}_{case.case_number}.txt",
            mime_type='text/plain',
            file_size=len(rendered.encode('utf-8')),
            document_type='report',
            description=f"Generated from template: {template.name}",
            classification='confidential',
            uploaded_by=current_user.id
        )
        db.session.add(doc)
        db.session.flush()
        
        # Save the report content
        doc_path = f"static/uploads/{doc.filename}"
        import os
        os.makedirs(os.path.dirname(doc_path), exist_ok=True)
        with open(doc_path, 'w') as f:
            f.write(rendered)
        
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='document',
            entity_id=doc.id,
            ip_address=request.remote_addr,
            case_id=case.id,
            description=f"Generated report: {template.name}"
        )
        db.session.commit()
        
        flash(f'Report generated and saved.', 'success')
        return redirect(url_for('cms.view_case', case_id=case.id))
    
    return render_template('cms/templates/generate_report.html', case=case, templates=templates)


def _build_report_context(case: Case):
    """Build context dictionary for template rendering."""
    context = {
        'case': None,
        'client': None,
        'subjects': [],
        'findings': [],
        'financials': {'summary': {}, 'by_type': {}},
        'user': current_user,
        'now': datetime.utcnow()
    }
    
    if case:
        case.decrypt_all() if hasattr(case, 'decrypt_all') else None
        
        context['case'] = {
            'case_number': case.case_number,
            'title': case.title,
            'description': case.description,
            'case_type': case.case_type,
            'priority': case.priority,
            'status': case.status,
            'start_date': case.start_date,
            'target_end_date': case.target_end_date,
            'client': {'name': case.client.name} if case.client else None
        }
        
        context['subjects'] = []
        for subject in case.subjects.all():
            subject.decrypt_identifiers()
            context['subjects'].append({
                'name': subject.name,
                'subject_type': subject.subject_type,
                'risk_score': subject.risk_score,
                'address': subject.address,
                'email': subject.email,
                'phone': subject.phone
            })
        
        context['findings'] = []
        for finding in case.findings.filter_by(is_deleted=False).all():
            context['findings'].append({
                'title': finding.title,
                'description': finding.content,  # Finding uses 'content' not 'description'
                'finding_type': finding.finding_type,
                'severity': finding.confidence_level or 'medium',  # Map confidence_level to severity
                'status': 'active'
            })
        
        # Financial summary
        fin_records = case.financial_records.filter_by(is_deleted=False).all()
        total = sum(r.amount for r in fin_records)
        by_type = {}
        for r in fin_records:
            if r.transaction_type not in by_type:
                by_type[r.transaction_type] = {'count': 0, 'total': 0}
            by_type[r.transaction_type]['count'] += 1
            by_type[r.transaction_type]['total'] += float(r.amount)
        
        context['financials'] = {
            'summary': {'total_records': len(fin_records), 'total_amount': total},
            'by_type': by_type
        }
    
    return context


@cms_bp.route('/templates/api/all')
@login_required
def get_all_templates():
    """Get all templates as JSON."""
    templates = DocumentTemplate.query.filter_by(is_active=True).order_by(DocumentTemplate.name).all()
    return jsonify({'templates': [t.to_dict() for t in templates]})


@cms_bp.route('/templates/api/render-preview', methods=['POST'])
@login_required
def render_template_preview():
    """Render a template preview with case data."""
    data = request.get_json()
    
    template_id = data.get('template_id')
    case_id = data.get('case_id')
    
    template = DocumentTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404
    
    case = Case.query.get(case_id) if case_id else None
    
    context = _build_report_context(case)
    context.update({
        'conclusion': data.get('conclusion', ''),
        'recommendation': data.get('recommendation', ''),
        'classification': data.get('classification', 'Confidential')
    })
    context['user'] = current_user
    
    rendered = template.render(context)
    
    return jsonify({'rendered': rendered})


# =============================================================================
# OSINT Background Search Routes
# =============================================================================

def run_osint_search(search_id: str, case_id: str, query: str, name: str):
    """Run OSINT search in background thread."""
    from app import person_dorks_search
    
    search_info = search_manager.get_search(search_id)
    if not search_info:
        return
    
    cancel_event = search_info['cancel_event']
    results = None
    
    try:
        logger.info(f"OSINT search {search_id} started for query: {name}")
        
        # Run the dorks search
        results = person_dorks_search(name)
        
        # Check if cancelled before setting results
        if cancel_event.is_set():
            logger.info(f"OSINT search {search_id} was cancelled")
            search_manager.cleanup(search_id)
            return
        
        # Count results
        total_results = 0
        if results and 'categories' in results:
            for cat, items in results.get('categories', {}).items():
                total_results += len(items) if items else 0
        
        # Set results
        search_manager.set_results(search_id, results)
        logger.info(f"OSINT search {search_id} completed with {total_results} dork results, {len(results.get('search_links', []))} search links")
        
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
    
    if search_info['status'] == 'completed':
        return jsonify({
            'search_id': search_id,
            'status': 'completed',
            'message': 'Search already completed'
        })
    
    if search_info['status'] == 'cancelled':
        return jsonify({
            'search_id': search_id,
            'status': 'cancelled',
            'message': 'Search already cancelled'
        })
    
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
    
    category_labels = {
        'social_media': 'Social Media',
        'files': 'Document',
        'news': 'News',
        'people_search': 'People Search',
        'general': 'Website',
        'search_link': 'Search Link'
    }
    
    for result in selected_results:
        domain = result.get('domain', 'Unknown')
        query = result.get('query', '')
        source = result.get('source', '')
        category = result.get('category', 'general')
        
        # Construct meaningful title based on what's available
        category_label = category_labels.get(category, category.replace('_', ' ').title())
        
        if source == 'search_link':
            # Search links show the engine name
            title = f"OSINT: {domain} - Search Link"
        elif query:
            # Extract key part of query (first 40 chars max)
            query_short = query[:40] + "..." if len(query) > 40 else query
            title = f"OSINT: {domain} - {query_short}"
        else:
            title = f"OSINT: {domain}"
        
        # Create content with full details
        content_parts = []
        if query:
            content_parts.append(f"Search Query: {query}")
        content_parts.append(f"Source: {source.upper() if source else 'Unknown'}")
        content_parts.append(f"URL: {result.get('url', 'N/A')}")
        content = '\n'.join(content_parts)
        
        # Build tags
        tags = ['osint', source.lower() if source else 'unknown']
        if category:
            tags.append(category.lower())
        if domain:
            tags.append(domain.split('.')[0])  # e.g., 'linkedin' from 'linkedin.com'
        
        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=title,
            content=content,
            source_url=result.get('url', ''),
            source_type='osint',
            finding_type='identity',
            reliability_score=5,
            confidence_level='medium',
            created_by=current_user.id,
            tags=tags
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
# Screenshot Routes
# =============================================================================

import io
from PIL import Image

UPLOAD_FOLDER = 'uploads'
SCREENSHOT_FOLDER = 'screenshots'

def get_screenshot_path(case_id: str, filename: str = None) -> str:
    """Get the path for screenshot storage."""
    base_path = os.path.join(current_app.root_path, 'static', UPLOAD_FOLDER, 'cases', case_id, SCREENSHOT_FOLDER)
    if filename:
        return os.path.join(base_path, filename)
    return base_path


@cms_bp.route('/cases/<case_id>/screenshots')
@login_required
@case_access_required
def list_screenshots(case_id: str):
    """List all screenshots for a case."""
    case = Case.query.get_or_404(case_id)
    screenshots = Screenshot.query.filter_by(case_id=case_id).order_by(Screenshot.created_at.desc()).all()
    
    return jsonify({
        'screenshots': [s.to_dict() for s in screenshots],
        'count': len(screenshots)
    })


@cms_bp.route('/cases/<case_id>/screenshots/upload', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def upload_screenshot(case_id: str):
    """Upload a screenshot file for a case."""
    case = Case.query.get_or_404(case_id)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file type
    if not file.content_type or not file.content_type.startswith('image/'):
        return jsonify({'error': 'File must be an image'}), 400
    
    # Create screenshot directory
    screenshot_dir = get_screenshot_path(case_id)
    os.makedirs(screenshot_dir, exist_ok=True)
    
    # Generate unique filename
    screenshot_id = str(uuid.uuid4())
    file_ext = 'png'
    filename = f"{screenshot_id}.{file_ext}"
    filepath = os.path.join(screenshot_dir, filename)
    
    try:
        # Save the file
        file.save(filepath)
        file_size = os.path.getsize(filepath)
        
        # Get URL from form
        url = request.form.get('url', '')
        
        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=url.split('/')[-1][:300] if url else f'Screenshot {screenshot_id[:8]}',
            file_size=file_size,
            created_by=current_user.id
        )
        
        db.session.add(screenshot)
        
        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='screenshot',
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Uploaded screenshot: {url or 'No URL'}"
        )
        
        db.session.commit()
        
        return jsonify({
            'message': 'Screenshot uploaded successfully',
            'screenshot': screenshot.to_dict()
        }), 201
        
    except Exception as e:
        # Clean up file if database insert failed
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/cases/<case_id>/screenshots/capture', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def capture_screenshot(case_id: str):
    """
    Capture a screenshot of a URL and save it.
    Note: This requires Playwright or similar to be installed.
    For now, this returns an error indicating the feature needs setup.
    """
    case = Case.query.get_or_404(case_id)
    data = request.get_json()
    
    if not data or not data.get('url'):
        return jsonify({'error': 'URL is required'}), 400
    
    url = data.get('url')
    title = data.get('title', '')
    
    # Create screenshot directory
    screenshot_dir = get_screenshot_path(case_id)
    os.makedirs(screenshot_dir, exist_ok=True)
    
    # Generate unique filename
    screenshot_id = str(uuid.uuid4())
    filename = f"{screenshot_id}.png"
    filepath = os.path.join(screenshot_dir, filename)
    
    try:
        # Try to use Playwright for screenshot capture
        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 720})
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(2000)  # Extra wait for dynamic content
                page.screenshot(path=filepath, full_page=False)
                
                # Get page title if not provided
                if not title:
                    title = page.title()[:300]
                
                browser.close()
            
            file_size = os.path.getsize(filepath)
            
        except ImportError:
            # Playwright not installed - try selenium as fallback
            return jsonify({
                'error': 'Screenshot capture not available. No screenshot library installed.',
                'setup_required': True,
                'message': 'Install playwright: pip install playwright && playwright install chromium'
            }), 503
            
        except Exception as e:
            return jsonify({
                'error': f'Failed to capture screenshot: {str(e)}',
                'setup_required': False
            }), 500
        
        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=title,
            file_size=file_size,
            created_by=current_user.id
        )
        
        db.session.add(screenshot)
        
        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='screenshot',
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Captured screenshot from: {url}"
        )
        
        db.session.commit()
        
        return jsonify({
            'message': 'Screenshot captured successfully',
            'screenshot': screenshot.to_dict()
        }), 201
        
    except Exception as e:
        # Clean up file if database insert failed
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>/thumbnail')
@login_required
@case_access_required
def get_screenshot_thumbnail(case_id: str, screenshot_id: str):
    """Get a thumbnail version of a screenshot."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()
    
    if not screenshot:
        return '', 404
    
    filepath = get_screenshot_path(case_id, screenshot.filename)
    
    if not os.path.exists(filepath):
        return '', 404
    
    try:
        # Generate thumbnail on the fly
        img = Image.open(filepath)
        img.thumbnail((200, 200), Image.Resampling.LANCZOS)
        
        thumb_io = io.BytesIO()
        img.save(thumb_io, format='PNG')
        thumb_io.seek(0)
        
        return send_file(
            thumb_io,
            mimetype='image/png',
            as_attachment=False,
            download_name=f"thumb_{screenshot.filename}"
        )
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return '', 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>/view')
@login_required
@case_access_required
def view_screenshot(case_id: str, screenshot_id: str):
    """View the full screenshot."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()
    
    if not screenshot:
        return '', 404
    
    filepath = get_screenshot_path(case_id, screenshot.filename)
    
    if not os.path.exists(filepath):
        return '', 404
    
    return send_file(
        filepath,
        mimetype='image/png',
        as_attachment=False,
        download_name=screenshot.title or screenshot.filename
    )


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>')
@login_required
@case_access_required
def get_screenshot(case_id: str, screenshot_id: str):
    """Get screenshot details."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()
    
    if not screenshot:
        return jsonify({'error': 'Screenshot not found'}), 404
    
    return jsonify(screenshot.to_dict())


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


# =============================================================================
# Reminder Routes
# =============================================================================

@cms_bp.route('/reminders')
@login_required
def reminders():
    """List all reminders for current user."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    filter_type = request.args.get('filter', 'all')
    
    query = Reminder.query.filter(
        Reminder.is_deleted == False
    )
    
    # Filter by status
    if filter_type == 'overdue':
        query = query.filter(
            Reminder.is_completed == False,
            Reminder.reminder_date < datetime.utcnow()
        )
    elif filter_type == 'upcoming':
        query = query.filter(
            Reminder.is_completed == False,
            Reminder.reminder_date >= datetime.utcnow()
        )
    elif filter_type == 'completed':
        query = query.filter(Reminder.is_completed == True)
    elif filter_type == 'mine':
        query = query.filter(Reminder.assigned_to == current_user.id)
    
    pagination = query.order_by(Reminder.reminder_date.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Stats
    now = datetime.utcnow()
    stats = {
        'total': Reminder.query.filter_by(is_deleted=False, is_completed=False).count(),
        'overdue': Reminder.query.filter(
            Reminder.is_deleted == False,
            Reminder.is_completed == False,
            Reminder.reminder_date < now
        ).count(),
        'today': Reminder.query.filter(
            Reminder.is_deleted == False,
            Reminder.is_completed == False,
            db.func.date(Reminder.reminder_date) == now.date()
        ).count()
    }
    
    return render_template('cms/reminders/list.html',
        reminders=pagination.items,
        pagination=pagination,
        filter_type=filter_type,
        stats=stats
    )


@cms_bp.route('/reminders/create', methods=['GET', 'POST'])
@login_required
def create_reminder():
    """Create a new reminder."""
    # Get related entities if specified
    case_id = request.args.get('case_id')
    subject_id = request.args.get('subject_id')
    client_id = request.args.get('client_id')
    
    case = Case.query.get(case_id) if case_id else None
    subject = Subject.query.get(subject_id) if subject_id else None
    client = Client.query.get(client_id) if client_id else None
    
    # Get users for assignment dropdown
    users = User.query.filter_by(is_active=True).all()
    
    # Calculate default reminder date (1 hour from now)
    from datetime import timedelta
    default_reminder = datetime.utcnow() + timedelta(hours=1)
    default_reminder_date = default_reminder.strftime('%Y-%m-%dT%H:%M')
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        title = data.get('title')
        if not title:
            if request.is_json:
                return jsonify({'error': 'Title is required'}), 400
            flash('Title is required.', 'danger')
            return render_template('cms/reminders/create.html',
                case=case, subject=subject, client=client, users=users,
                default_reminder_date=default_reminder_date)
        
        # Parse reminder date
        reminder_date_str = data.get('reminder_date')
        if reminder_date_str:
            try:
                reminder_date = datetime.strptime(reminder_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                try:
                    reminder_date = datetime.strptime(reminder_date_str, '%Y-%m-%d %H:%M')
                except ValueError:
                    reminder_date = datetime.utcnow()
        else:
            reminder_date = datetime.utcnow()
        
        # Parse due date if provided
        due_date_str = data.get('due_date')
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d')
            except ValueError:
                due_date = None
        
        reminder = Reminder(
            title=title,
            description=data.get('description', ''),
            reminder_date=reminder_date,
            due_date=due_date,
            reminder_type=data.get('reminder_type', ReminderType.MANUAL.value),
            recurrence=data.get('recurrence', ReminderRecurrence.NONE.value),
            priority=data.get('priority', 'medium'),
            case_id=data.get('case_id') or case_id,
            subject_id=data.get('subject_id') or subject_id,
            client_id=data.get('client_id') or client_id,
            assigned_to=data.get('assigned_to') or None,
            created_by=current_user.id,
            notify_email=data.get('notify_email') in ['on', 'true', '1', True],
            notify_dashboard=data.get('notify_dashboard') in ['on', 'true', '1', True]
        )
        
        db.session.add(reminder)
        
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='reminder',
            entity_id=reminder.id,
            ip_address=request.remote_addr,
            description=f"Created reminder: {reminder.title}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'Reminder created', 'reminder': reminder.to_dict()}), 201
        
        flash('Reminder created.', 'success')
        return redirect(url_for('cms.reminders'))
    
    return render_template('cms/reminders/create.html',
        case=case, subject=subject, client=client, users=users,
        default_reminder_date=default_reminder_date)


@cms_bp.route('/reminders/<reminder_id>')
@login_required
def view_reminder(reminder_id: str):
    """View reminder details."""
    reminder = Reminder.query.get_or_404(reminder_id)
    
    # Get related case if available
    case = Case.query.get(reminder.case_id) if reminder.case_id else None
    
    AuditLog.log(
        user_id=current_user.id,
        action='read',
        entity_type='reminder',
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Viewed reminder: {reminder.title}"
    )
    db.session.commit()
    
    return render_template('cms/reminders/view.html',
        reminder=reminder, case=case)


@cms_bp.route('/reminders/<reminder_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_reminder(reminder_id: str):
    """Edit a reminder."""
    reminder = Reminder.query.get_or_404(reminder_id)
    users = User.query.filter_by(is_active=True).all()
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        old_values = {}
        changes = {}
        
        # Update fields
        fields = ['title', 'description', 'priority', 'reminder_type', 'recurrence', 'assigned_to']
        for field in fields:
            if field in data:
                old_val = getattr(reminder, field)
                new_val = data[field]
                if old_val != new_val:
                    old_values[field] = old_val
                    changes[field] = {'old': old_val, 'new': new_val}
                    setattr(reminder, field, new_val)
        
        # Parse and update dates
        reminder_date_str = data.get('reminder_date')
        if reminder_date_str:
            try:
                reminder.reminder_date = datetime.strptime(reminder_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass
        
        due_date_str = data.get('due_date')
        if due_date_str:
            try:
                reminder.due_date = datetime.strptime(due_date_str, '%Y-%m-%d')
            except ValueError:
                reminder.due_date = None
        elif 'due_date' in data and not due_date_str:
            reminder.due_date = None
        
        # Update notification settings
        reminder.notify_email = data.get('notify_email') in ['on', 'true', '1', True]
        reminder.notify_dashboard = data.get('notify_dashboard') in ['on', 'true', '1', True]
        
        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='reminder',
            entity_id=reminder_id,
            changes=changes if changes else None,
            ip_address=request.remote_addr,
            description=f"Updated reminder: {reminder.title}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'Reminder updated', 'reminder': reminder.to_dict()})
        
        flash('Reminder updated.', 'success')
        return redirect(url_for('cms.view_reminder', reminder_id=reminder_id))
    
    return render_template('cms/reminders/edit.html',
        reminder=reminder, users=users)


@cms_bp.route('/reminders/<reminder_id>/complete', methods=['POST'])
@login_required
def complete_reminder(reminder_id: str):
    """Mark a reminder as completed."""
    reminder = Reminder.query.get_or_404(reminder_id)
    
    reminder.complete()
    
    AuditLog.log(
        user_id=current_user.id,
        action='complete',
        entity_type='reminder',
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Completed reminder: {reminder.title}"
    )
    db.session.commit()
    
    if request.is_json:
        return jsonify({'message': 'Reminder completed', 'reminder': reminder.to_dict()})
    
    flash('Reminder marked as completed.', 'success')
    
    # Check if there's a return URL
    return_url = request.args.get('return_url')
    if return_url:
        return redirect(return_url)
    return redirect(url_for('cms.reminders'))


@cms_bp.route('/reminders/<reminder_id>/snooze', methods=['POST'])
@login_required
def snooze_reminder(reminder_id: str):
    """Snooze a reminder."""
    reminder = Reminder.query.get_or_404(reminder_id)
    
    minutes = request.args.get('minutes', 30, type=int)
    reminder.snooze(minutes=minutes)
    
    AuditLog.log(
        user_id=current_user.id,
        action='snooze',
        entity_type='reminder',
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Snoozed reminder: {reminder.title} for {minutes} minutes"
    )
    db.session.commit()
    
    if request.is_json:
        return jsonify({'message': 'Reminder snoozed', 'reminder': reminder.to_dict()})
    
    flash(f'Reminder snoozed for {minutes} minutes.', 'info')
    
    return_url = request.args.get('return_url')
    if return_url:
        return redirect(return_url)
    return redirect(url_for('cms.reminders'))


@cms_bp.route('/reminders/<reminder_id>/delete', methods=['POST'])
@login_required
def delete_reminder(reminder_id: str):
    """Delete a reminder."""
    reminder = Reminder.query.get_or_404(reminder_id)
    
    reminder.soft_delete()
    
    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='reminder',
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Deleted reminder: {reminder.title}"
    )
    db.session.commit()
    
    if request.is_json:
        return jsonify({'message': 'Reminder deleted'})
    
    flash('Reminder deleted.', 'info')
    return redirect(url_for('cms.reminders'))


@cms_bp.route('/api/reminders/check-overdue')
@login_required
def api_check_overdue():
    """API endpoint to check and update overdue reminders."""
    now = datetime.utcnow()
    
    overdue = Reminder.query.filter(
        Reminder.is_deleted == False,
        Reminder.is_completed == False,
        Reminder.reminder_date < now
    ).all()
    
    count = 0
    for r in overdue:
        if not r.is_overdue:
            r.is_overdue = True
            count += 1
    
    if count > 0:
        db.session.commit()
    
    return jsonify({
        'overdue_count': len(overdue),
        'newly_overdue': count
    })
