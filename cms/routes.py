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

from flask import Response
import io
import csv
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, date
from typing import Optional, Dict, Any
from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, flash, current_app, send_file, abort
)
from flask_login import login_required, current_user

from .models import (
    db, Case, Client, Subject, Finding, FinancialRecord,
    AuditLog, Document, User, CaseStatus, CasePriority,
    subject_relations, Comment,
    CommentEditHistory, DocumentTemplate, Reminder, ReminderType, ReminderRecurrence,
    Screenshot, Setting, SpiderFootScan, Address, Contact, init_default_settings,
    OsintSearch
)
from .auth import (
    roles_required, admin_required, senior_required,
    can_export, case_access_required, case_edit_required
)
from .encryption_utils import encryptor

try:
    from .spiderfoot_service import SpiderFootService
    SPIDERFOOT_AVAILABLE = True
except ImportError:
    SPIDERFOOT_AVAILABLE = False
    SpiderFootService = None

try:
    from .vessel_service import lookup_vessel
    VESSEL_SERVICE_AVAILABLE = True
except ImportError:
    VESSEL_SERVICE_AVAILABLE = False
    lookup_vessel = None


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
    """Manages background OSINT searches — DB-backed for multi-worker gunicorn.

    Cancel events stay in-memory (per-worker) since threads share the same process.
    All persistent state (status, results, timestamps) lives in the database.
    """

    def __init__(self):
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create_search(self, case_id: str, search_id: str, query: str, subject_id: str = None) -> threading.Event:
        """Create a new search: DB record + in-memory cancel event."""
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[search_id] = cancel_event

        row = OsintSearch(
            search_id=search_id,
            case_id=case_id,
            subject_id=subject_id,
            search_query=query,
            status='running',
            started_at=datetime.utcnow(),
        )
        db.session.add(row)
        db.session.commit()
        return cancel_event

    def get_search(self, search_id: str) -> Optional[Dict[str, Any]]:
        """Get search info (DB row + in-memory cancel event)."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return None
        with self._lock:
            cancel_event = self._cancel_events.get(search_id)
        return {
            'cancel_event': cancel_event,
            'db_row': row,
            'search_id': row.search_id,
            'case_id': row.case_id,
            'query': row.search_query,
            'status': row.status,
            'results': row.results,
        }

    def set_results(self, search_id: str, results: Any):
        """Persist results to DB."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return
        row.results = results
        row.status = 'completed'
        row.completed_at = datetime.utcnow()
        db.session.commit()

    def set_error(self, search_id: str, error: str):
        """Persist error state to DB."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return
        row.status = 'failed'
        row.error = error
        row.completed_at = datetime.utcnow()
        db.session.commit()

    def cancel_search(self, search_id: str) -> bool:
        """Cancel a running search — sets cancel event + DB state."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return False

        with self._lock:
            cancel_event = self._cancel_events.get(search_id)
        if cancel_event:
            cancel_event.set()

        if row.status == 'running':
            row.status = 'cancelled'
            row.cancelled_at = datetime.utcnow()
            db.session.commit()
        return True

    def cleanup(self, search_id: str):
        """Remove in-memory cancel event. DB row stays for history."""
        with self._lock:
            self._cancel_events.pop(search_id, None)

    def get_status(self, search_id: str) -> Optional[Dict[str, Any]]:
        """Get current search status from DB."""
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        if not row:
            return None
        return row.get_status_dict()

    def is_cancelled(self, search_id: str) -> bool:
        """Check if search was cancelled (in-memory event + DB)."""
        with self._lock:
            cancel_event = self._cancel_events.get(search_id)
        if cancel_event and cancel_event.is_set():
            return True
        row = OsintSearch.query.filter_by(search_id=search_id).first()
        return row.status == 'cancelled' if row else False


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
    from sqlalchemy import func
    # instr on SQLite, strpos on PostgreSQL
    _instr = func.instr if db.engine.dialect.name == 'sqlite' else func.strpos
    case_type_stats = db.session.query(
        func.substr(Case.case_type, 1, _instr(
            Case.case_type, '|') - 1).label('code'),
        func.count(Case.id).label('count')
    ).filter(
        Case.is_deleted == False,
        Case.case_type.isnot(None),
        Case.case_type != '',
        Case.case_type.like('%|%')  # Only show cases with criminal code format
    ).group_by(
        func.substr(Case.case_type, 1, _instr(Case.case_type, '|') - 1)
    ).order_by(func.count(Case.id).desc()).limit(10).all()

    case_type_labels = [
        s.code if s.code else 'Unknown' for s in case_type_stats]
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
        Case.priority.in_(
            [CasePriority.CRITICAL.value, CasePriority.HIGH.value]),
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

@cms_bp.route('/clients')
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def clients():
    """List all clients."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '')
    show_archived = request.args.get(
        'show_archived', '').lower() in ('1', 'true', 'yes')
    sort = request.args.get('sort', 'name')
    order = request.args.get('order', 'asc')

    query = Client.query.filter_by(is_deleted=False)
    if not show_archived:
        query = query.filter_by(is_active=True)

    if search:
        query = query.filter(Client.name.ilike(f'%{search}%'))

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
                           order=order,
                           show_archived=show_archived
                           )


@cms_bp.route('/clients/<client_id>')
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def view_client(client_id: str):
    """View client details with all associated cases."""
    client = Client.query.get_or_404(client_id)
    client.decrypt_naw()  # Decrypt for display
    for c in client.contacts:
        c.decrypt_fields()
    for addr in client.addresses:
        addr.decrypt_fields()

    cases = Case.query.filter_by(
        client_id=client_id,
        is_deleted=False
    ).order_by(Case.created_at.desc()).all()

    active_cases_count = Case.query.filter(
        Case.client_id == client_id,
        Case.is_deleted == False,
        Case.status.in_(['open', 'active', 'suspended'])
    ).count()

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

    return render_template('cms/clients/view.html', client=client, cases=cases, active_cases_count=active_cases_count)


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
                flash(
                    f'Warning: A client with this name already exists: {exact_match["name"]}', 'warning')
                return render_template('cms/clients/create.html',
                                       duplicate_warning=True,
                                       exact_match=exact_match,
                                       similar_clients=similar[:5],
                                       submitted_name=name,
                                       submitted_is_company=bool(data.get('is_company')))

            if similar and not request.is_json:
                flash(
                    'Warning: Similar clients found. Please review before creating.', 'warning')
                return render_template('cms/clients/create.html',
                                       duplicate_warning=True,
                                       similar_clients=similar[:5],
                                       submitted_name=name,
                                       submitted_is_company=bool(data.get('is_company')))

        client = Client(name=name)
        client.is_company = bool(data.get('is_company'))

        # Set encrypted fields
        encrypted_fields = ['contact_person', 'contact_email', 'contact_phone',
                            'social_security_number', 'bank_account',
                            'date_of_birth', 'place_of_birth']
        for field in encrypted_fields:
            if data.get(field):
                setattr(client, field, encryptor.encrypt(data[field]))

        # Handle structured contacts
        if data.get('contacts_data'):
            try:
                contacts_data = json.loads(data['contacts_data']) if isinstance(
                    data['contacts_data'], str) else data['contacts_data']
                for c_data in contacts_data:
                    if c_data.get('value'):
                        contact = Contact(
                            client_id=client.id,
                            contact_type=c_data.get('contact_type', 'email'),
                            value=c_data.get('value'),
                            is_primary=c_data.get('is_primary', False)
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Also set legacy fields for backward compat
                        if c_data.get('contact_type') == 'email' and c_data.get('is_primary'):
                            client.contact_email = encryptor.encrypt(
                                c_data.get('value')) if c_data.get('value') else None
                        elif c_data.get('contact_type') == 'phone' and c_data.get('is_primary'):
                            client.contact_phone = encryptor.encrypt(
                                c_data.get('value')) if c_data.get('value') else None
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Handle structured addresses
        if data.get('addresses_data'):
            try:
                addresses_data = json.loads(data['addresses_data']) if isinstance(
                    data['addresses_data'], str) else data['addresses_data']
                for addr_data in addresses_data:
                    if addr_data.get('street') or addr_data.get('zipcode'):
                        address = Address(
                            client_id=client.id,
                            street=addr_data.get('street'),
                            number=addr_data.get('number'),
                            zipcode=addr_data.get('zipcode'),
                            town=addr_data.get('town'),
                            country=addr_data.get('country', 'Netherlands'),
                            is_primary=addr_data.get('is_primary', False)
                        )
                        address.encrypt_fields()
                        db.session.add(address)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

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
            changes['is_company'] = {
                'old': client.is_company, 'new': new_is_company}
            client.is_company = new_is_company

        # Update encrypted fields
        encrypted_fields = ['contact_person', 'contact_email', 'contact_phone',
                            'social_security_number', 'bank_account',
                            'date_of_birth', 'place_of_birth']
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(client, field)
                if new_value != old_value:
                    changes[field] = {
                        'old': '[encrypted]', 'new': '[encrypted]'}
                    if new_value:
                        setattr(client, field, encryptor.encrypt(new_value))
                    else:
                        setattr(client, field, None)

        # Handle structured contacts
        if data.get('contacts_data'):
            try:
                contacts_data = json.loads(data['contacts_data']) if isinstance(
                    data['contacts_data'], str) else data['contacts_data']
                old_contacts = list(client.contacts)
                for c in old_contacts:
                    db.session.delete(c)
                for c_data in contacts_data:
                    if c_data.get('value'):
                        contact = Contact(
                            client_id=client.id,
                            contact_type=c_data.get('contact_type', 'email'),
                            value=c_data.get('value'),
                            is_primary=c_data.get('is_primary', False)
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Update legacy fields for backward compat
                        if c_data.get('contact_type') == 'email' and c_data.get('is_primary'):
                            client.contact_email = encryptor.encrypt(
                                c_data.get('value')) if c_data.get('value') else None
                        elif c_data.get('contact_type') == 'phone' and c_data.get('is_primary'):
                            client.contact_phone = encryptor.encrypt(
                                c_data.get('value')) if c_data.get('value') else None
                changes['contacts'] = {
                    'old': f'{len(old_contacts)} contact(s)', 'new': f'{len(contacts_data)} contact(s)'}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Handle structured addresses
        if data.get('addresses_data'):
            try:
                addresses_data = json.loads(data['addresses_data']) if isinstance(
                    data['addresses_data'], str) else data['addresses_data']
                old_addresses = list(client.addresses)
                for addr in old_addresses:
                    db.session.delete(addr)
                for addr_data in addresses_data:
                    if addr_data.get('street') or addr_data.get('zipcode'):
                        address = Address(
                            client_id=client.id,
                            street=addr_data.get('street'),
                            number=addr_data.get('number'),
                            zipcode=addr_data.get('zipcode'),
                            town=addr_data.get('town'),
                            country=addr_data.get('country', 'Netherlands'),
                            is_primary=addr_data.get('is_primary', False)
                        )
                        address.encrypt_fields()
                        db.session.add(address)
                changes['addresses'] = {
                    'old': f'{len(old_addresses)} address(es)', 'new': f'{len(addresses_data)} address(es)'}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Update contract info
        if data.get('contract_number') != client.contract_number:
            changes['contract_number'] = {
                'old': client.contract_number, 'new': data.get('contract_number')}
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
    for c in client.contacts:
        c.decrypt_fields()
    for addr in client.addresses:
        addr.decrypt_fields()
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
    """Archive a client if no active cases exist."""
    client = Client.query.get_or_404(client_id)

    if not client.is_active:
        return jsonify({'error': 'Client is already archived'}), 400

    # Check if client has any non-closed/non-archived cases
    active_cases = Case.query.filter(
        Case.client_id == client_id,
        Case.is_deleted == False,
        Case.status.in_(['open', 'active', 'suspended'])
    ).count()
    if active_cases > 0:
        return jsonify({'error': f'Kan niet archiveren: client heeft {active_cases} actieve za(a)k(en)'}), 400

    client.is_active = False

    AuditLog.log(
        user_id=current_user.id,
        action='archive',
        entity_type='client',
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Archived client: {client.name}"
    )
    db.session.commit()

    flash(f'Client {client.name} is gearchiveerd.', 'info')

    if request.is_json:
        return jsonify({'message': 'Client archived'})
    return redirect(url_for('cms.clients'))


@cms_bp.route('/clients/<client_id>/restore', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def restore_client(client_id: str):
    """Restore an archived client."""
    client = Client.query.get_or_404(client_id)

    if client.is_active:
        return jsonify({'error': 'Client is already active'}), 400

    client.is_active = True

    AuditLog.log(
        user_id=current_user.id,
        action='restore',
        entity_type='client',
        entity_id=client_id,
        ip_address=request.remote_addr,
        description=f"Restored client: {client.name}"
    )
    db.session.commit()

    flash(f'Client {client.name} is hersteld.', 'info')

    if request.is_json:
        return jsonify({'message': 'Client restored'})
    return redirect(url_for('cms.view_client', client_id=client.id))


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
                           filters={'status': status, 'priority': priority, 'search': search, 'client': client_filter,
                                    'assigned': assigned, 'sort': sort, 'order': order, 'case_type': case_type_filter}
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

    financials = case.financial_records.filter_by(is_deleted=False).order_by(
        FinancialRecord.transaction_date.desc()).all()

    # Get documents
    documents = Document.query.filter_by(
        case_id=case_id, is_deleted=False).order_by(Document.created_at.desc()).all()

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
            {'id': c.id, 'case_number': c.case_number,
                'title': c.title, 'status': c.status}
            for c in case.child_cases.filter_by(is_deleted=False)
        ]
    })


@cms_bp.route('/api/cases/<case_id>/audit-log')
@login_required
def get_case_audit_log_api(case_id: str):
    """Get audit log for a case via API."""
    Case.query.get_or_404(case_id)
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
                    changes[field] = {'old': str(
                        old_value) if old_value else None, 'new': str(new_value)}
                    setattr(case, field, new_value)

        # Handle tags (comma-separated string to list)
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

        # Handle lead_investigator_id
        new_lead = data.get('lead_investigator_id') or None
        if new_lead != case.lead_investigator_id:
            changes['lead_investigator_id'] = {
                'old': case.lead_investigator_id, 'new': new_lead}
            case.lead_investigator_id = new_lead

        # Handle target_end_date separately (needs date conversion)
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
        from .models import SocialAccount
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
                           filters={'search': search, 'type': subject_type,
                                    'sort': sort, 'order': order}
                           )


@cms_bp.route('/subjects/<subject_id>')
@login_required
def view_subject(subject_id: str):
    """View subject details."""
    subject = Subject.query.get_or_404(subject_id)
    subject.decrypt_identifiers()
    # Parse vessel_data to dict for template (SQLite JSON column may return string)
    vd = subject.vessel_data
    while isinstance(vd, str):
        try:
            vd = json.loads(vd)
        except (json.JSONDecodeError, TypeError):
            try:
                import ast
                vd = ast.literal_eval(vd)
            except (ValueError, SyntaxError, TypeError):
                vd = {}
    subject.vessel_data = vd if isinstance(vd, dict) else {}
    for addr in subject.addresses:
        addr.decrypt_fields()
    for c in subject.contacts:
        c.decrypt_fields()

    financials = subject.financial_records.filter_by(is_deleted=False).all()
    findings = subject.findings.filter_by(
        is_deleted=False).order_by(Finding.created_at.desc()).all()

    # Get linked cases
    linked_cases = []
    first_case_id = None
    for case in Case.query.filter_by(is_deleted=False).all():
        if subject in case.subjects.all():
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
    writer.writerow(['Name', 'Type', 'Risk Score',
                    'Email', 'Phone', 'Address'])
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
    writer.writerow(['Title', 'Type', 'Severity',
                    'Status', 'Created', 'Description'])
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

    writer.writerow([
        'Name', 'Type', 'Risk Score', 'Email', 'Phone',
        'Address (old)', 'Street', 'Number', 'Zipcode', 'Town', 'Country',
        'Address Kadaster Verified',
        'Notes', 'Created'
    ])

    for subject in Subject.query.filter_by(is_deleted=False).order_by(Subject.name).all():
        subject.decrypt_identifiers()
        primary_addr = next(
            (a for a in list(subject.addresses) if a.is_primary), None)
        if primary_addr:
            primary_addr.decrypt_fields()

        writer.writerow([
            subject.name,
            subject.subject_type,
            subject.risk_score,
            subject.email or '',
            subject.phone or '',
            subject.address or '',
            primary_addr.street or '' if primary_addr else '',
            primary_addr.number or '' if primary_addr else '',
            primary_addr.zipcode or '' if primary_addr else '',
            primary_addr.town or '' if primary_addr else '',
            primary_addr.country or '' if primary_addr else '',
            'Yes' if primary_addr and primary_addr.kadaster_verified else 'No',
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

    writer.writerow(['Name', 'Type', 'Contact Person', 'Email',
                    'Phone', 'Contract Number', 'Active', 'Created'])

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

    writer.writerow(['Case Number', 'Title', 'Client', 'Status', 'Priority',
                    'Start Date', 'End Date', 'Type', 'Subjects Count', 'Findings Count'])

    for case in Case.query.filter_by(is_deleted=False).order_by(Case.case_number).all():
        writer.writerow([
            case.case_number,
            case.title,
            case.client.name if case.client else 'N/A',
            case.status,
            case.priority,
            case.start_date.strftime('%Y-%m-%d'),
            case.actual_end_date.strftime(
                '%Y-%m-%d') if case.actual_end_date else '',
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
                flash(
                    f'Warning: A subject with this name already exists: {exact_match["name"]}', 'warning')
                case_id = request.args.get('case_id')
                return render_template('cms/subjects/create.html',
                                       case_id=case_id,
                                       duplicate_warning=True,
                                       exact_match=exact_match,
                                       similar_subjects=similar[:5],
                                       submitted_name=name,
                                       submitted_type=data.get('subject_type'))

            if similar and not request.is_json:
                flash(
                    'Warning: Similar subjects found. Please review before creating.', 'warning')
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
            vehicle_type=data.get('vehicle_type'),
            imo_number=data.get('imo_number'),
            mmsi=data.get('mmsi'),
            eni_number=data.get('eni_number'),
            vessel_nationality=data.get('vessel_nationality')
        )

        if data['subject_type'] == 'vehicle':
            rdw_data = {}
            rdw_fields = [
                'handelsbenaming', 'voertuigsoort', 'eerste_kleur', 'tweede_kleur',
                'aantal_deuren', 'aantal_zitplaatsen', 'cilinderinhoud', 'aantal_cilinders',
                'massa_ledig', 'maximum_massa', 'vervaldatum_apk', 'wam_verzekerd',
                'taxi_indicator', 'export_indicator', 'europese_voertuigcategorie',
                'zuinigheidsclassificatie', 'catalogusprijs', 'datum_eerste_toelating'
            ]
            for field in rdw_fields:
                if data.get(field):
                    rdw_data[field] = data.get(field)

            if rdw_data or data.get('license_plate'):
                rdw_data['kenteken'] = data.get('license_plate', '').upper()
                rdw_data['merk'] = data.get('brand', '')
                rdw_data['inrichting'] = data.get('vehicle_type', '')
                if data.get('eerste_kleur'):
                    rdw_data['kleur'] = data.get('eerste_kleur')
                subject.rdw_data = rdw_data

        if data['subject_type'] == 'vessel' and data.get('vessel_data'):
            try:
                subject.vessel_data = json.loads(data['vessel_data'])
            except (json.JSONDecodeError, TypeError):
                subject.vessel_data = data['vessel_data']

        # Encrypt all identifying fields (person + vehicle + vessel)
        subject.encrypt_identifiers()

        db.session.add(subject)
        db.session.flush()  # Get subject ID before adding addresses

        # Handle structured addresses
        if data.get('addresses_data'):
            try:
                addresses_data = json.loads(data['addresses_data']) if isinstance(
                    data['addresses_data'], str) else data['addresses_data']
                for addr_data in addresses_data:
                    if addr_data.get('street') or addr_data.get('zipcode'):
                        address = Address(
                            subject_id=subject.id,
                            street=addr_data.get('street'),
                            number=addr_data.get('number'),
                            zipcode=addr_data.get('zipcode'),
                            town=addr_data.get('town'),
                            country=addr_data.get('country', 'Netherlands'),
                            is_primary=addr_data.get('is_primary', False)
                        )
                        address.encrypt_fields()
                        db.session.add(address)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Handle structured contacts
        if data.get('contacts_data'):
            try:
                contacts_data = json.loads(data['contacts_data']) if isinstance(
                    data['contacts_data'], str) else data['contacts_data']
                for c_data in contacts_data:
                    if c_data.get('value'):
                        contact = Contact(
                            subject_id=subject.id,
                            contact_type=c_data.get('contact_type', 'email'),
                            value=c_data.get('value'),
                            is_primary=c_data.get('is_primary', False)
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Also set legacy fields for backward compat
                        if c_data.get('contact_type') == 'email' and c_data.get('is_primary'):
                            subject.email = encryptor.encrypt(c_data.get(
                                'value')) if c_data.get('value') else None
                        elif c_data.get('contact_type') == 'phone' and c_data.get('is_primary'):
                            subject.phone = encryptor.encrypt(c_data.get(
                                'value')) if c_data.get('value') else None
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

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

        # Update subject_type if provided
        if 'subject_type' in data and data['subject_type'] and data['subject_type'] != subject.subject_type:
            changes['subject_type'] = {
                'old': subject.subject_type, 'new': data['subject_type']}
            subject.subject_type = data['subject_type']

        if 'risk_score' in data:
            changes['risk_score'] = {
                'old': subject.risk_score, 'new': data['risk_score']}
            subject.risk_score = int(data['risk_score'])

        if 'notes' in data:
            subject.notes = data['notes']

        # Update encrypted fields for persons
        encrypted_fields = ['date_of_birth', 'place_of_birth', 'nationality',
                            'identification_number', 'address', 'phone', 'email']
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                # Decrypt old value for comparison
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except Exception:
                    pass
                if new_value != old_value:
                    changes[field] = {
                        'old': old_value or '[empty]', 'new': new_value or '[empty]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Update vehicle fields
        # Encrypted vehicle fields
        encrypted_vehicle_fields = [
            'license_plate', 'vin', 'insurance_company']
        for field in encrypted_vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                # Decrypt old value for comparison
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except Exception:
                    pass
                if new_value != old_value:
                    changes[field] = {
                        'old': old_value or '[empty]', 'new': new_value or '[empty]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Non-encrypted vehicle fields
        non_encrypted_vehicle_fields = ['brand', 'vehicle_type']
        for field in non_encrypted_vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                if new_value != getattr(subject, field):
                    changes[field] = {'old': getattr(
                        subject, field) or '[empty]', 'new': new_value or '[empty]'}
                    setattr(subject, field, new_value)

        # Encrypted vessel fields
        encrypted_vessel_fields = ['imo_number',
                                   'mmsi', 'eni_number', 'vessel_nationality']
        for field in encrypted_vessel_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except Exception:
                    pass
                if new_value != old_value:
                    changes[field] = {
                        'old': old_value or '[empty]', 'new': new_value or '[empty]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Handle structured addresses
        if data.get('addresses_data'):
            try:
                addresses_data = json.loads(data['addresses_data']) if isinstance(
                    data['addresses_data'], str) else data['addresses_data']
                old_addresses = list(subject.addresses)
                addr_count_before = len(old_addresses)
                for addr in old_addresses:
                    db.session.delete(addr)
                for addr_data in addresses_data:
                    if addr_data.get('street') or addr_data.get('zipcode'):
                        address = Address(
                            subject_id=subject.id,
                            street=addr_data.get('street'),
                            number=addr_data.get('number'),
                            zipcode=addr_data.get('zipcode'),
                            town=addr_data.get('town'),
                            country=addr_data.get('country', 'Netherlands'),
                            is_primary=addr_data.get('is_primary', False)
                        )
                        address.encrypt_fields()
                        db.session.add(address)
                changes['addresses'] = {
                    'old': f'{addr_count_before} address(es)', 'new': f'{len(addresses_data)} address(es)'}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Handle structured contacts
        if data.get('contacts_data'):
            try:
                contacts_data = json.loads(data['contacts_data']) if isinstance(
                    data['contacts_data'], str) else data['contacts_data']
                old_contacts = list(subject.contacts)
                contact_count_before = len(old_contacts)
                for c in old_contacts:
                    db.session.delete(c)
                for c_data in contacts_data:
                    if c_data.get('value'):
                        contact = Contact(
                            subject_id=subject.id,
                            contact_type=c_data.get('contact_type', 'email'),
                            value=c_data.get('value'),
                            is_primary=c_data.get('is_primary', False)
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Update legacy fields for backward compat
                        if c_data.get('contact_type') == 'email' and c_data.get('is_primary'):
                            try:
                                current = encryptor.decrypt(
                                    subject.email) if subject.email else None
                            except Exception:
                                current = subject.email  # may already be plaintext
                            if c_data.get('value') != current:
                                subject.email = encryptor.encrypt(c_data.get(
                                    'value')) if c_data.get('value') else None
                        elif c_data.get('contact_type') == 'phone' and c_data.get('is_primary'):
                            try:
                                current = encryptor.decrypt(
                                    subject.phone) if subject.phone else None
                            except Exception:
                                current = subject.phone  # may already be plaintext
                            if c_data.get('value') != current:
                                subject.phone = encryptor.encrypt(c_data.get(
                                    'value')) if c_data.get('value') else None
                changes['contacts'] = {
                    'old': f'{contact_count_before} contact(s)', 'new': f'{len(contacts_data)} contact(s)'}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Update RDW data if provided
        rdw_fields = [
            'handelsbenaming', 'voertuigsoort', 'eerste_kleur', 'tweede_kleur',
            'aantal_deuren', 'aantal_zitplaatsen', 'cilinderinhoud', 'aantal_cilinders',
            'massa_ledig', 'maximum_massa', 'vervaldatum_apk', 'wam_verzekerd',
            'taxi_indicator', 'export_indicator', 'europese_voertuigcategorie',
            'zuinigheidsclassificatie', 'catalogusprijs', 'datum_eerste_toelating',
            'type', 'variant', 'uitvoering', 'typegoedkeuringsnummer', 'wielbasis'
        ]

        rdw_data = {}
        for field in rdw_fields:
            if data.get(field):
                rdw_data[field] = data[field]

        # Also store basic vehicle fields in RDW data
        if data.get('license_plate'):
            rdw_data['kenteken'] = data['license_plate']
        if data.get('brand'):
            rdw_data['merk'] = data['brand']
        if data.get('vehicle_type'):
            rdw_data['inrichting'] = data['vehicle_type']
        if data.get('vin'):
            rdw_data['chassisnummer'] = data['vin']

        if rdw_data:
            existing_rdw = subject.rdw_data or {}
            existing_rdw.update(rdw_data)
            subject.rdw_data = existing_rdw
            changes['rdw_data'] = {
                'old': 'updated', 'new': 'RDW fields updated'}

        # Update vessel data if provided
        if data.get('vessel_data'):
            try:
                subject.vessel_data = json.loads(data['vessel_data'])
            except (json.JSONDecodeError, TypeError):
                subject.vessel_data = data['vessel_data']
            changes['vessel_data'] = {
                'old': 'updated', 'new': 'Vessel data updated'}

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
    for addr in subject.addresses:
        addr.decrypt_fields()
    for c in subject.contacts:
        c.decrypt_fields()
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
    ext = file.filename.rsplit(
        '.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({'error': 'Only image files allowed'}), 400

    # Create upload directory
    upload_dir = os.path.join(current_app.root_path,
                              'static', 'uploads', 'subjects', subject_id)
    os.makedirs(upload_dir, exist_ok=True)

    # Remove old photo if exists
    if subject.photo_path:
        old_path = os.path.join(current_app.root_path,
                                'static', subject.photo_path.lstrip('/'))
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


@cms_bp.route('/subjects/<subject_id>/face-encoding', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def save_face_encoding(subject_id: str):
    """Save face encoding for a subject."""
    subject = Subject.query.get_or_404(subject_id)
    data = request.get_json()

    if not data or 'encoding' not in data:
        return jsonify({'error': 'No encoding provided'}), 400

    encoding = data['encoding']

    if not isinstance(encoding, list) or len(encoding) != 128:
        return jsonify({'error': 'Invalid encoding format'}), 400

    subject.face_encoding = encoding

    AuditLog.log(
        user_id=current_user.id,
        action='face_encoding_saved',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Saved face encoding for {subject.name}"
    )
    db.session.commit()

    return jsonify({
        'message': 'Face encoding saved',
        'has_encoding': True
    })


@cms_bp.route('/subjects/<subject_id>/face-encoding', methods=['DELETE'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def delete_face_encoding(subject_id: str):
    """Delete face encoding for a subject."""
    subject = Subject.query.get_or_404(subject_id)

    subject.face_encoding = None

    AuditLog.log(
        user_id=current_user.id,
        action='face_encoding_deleted',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Deleted face encoding for {subject.name}"
    )
    db.session.commit()

    return jsonify({
        'message': 'Face encoding deleted',
        'has_encoding': False
    })


@cms_bp.route('/subjects/compare-faces', methods=['POST'])
@login_required
def compare_faces():
    """Compare face encodings. Returns list of matching subjects."""
    data = request.get_json()

    if not data or 'encoding' not in data:
        return jsonify({'error': 'No encoding provided'}), 400

    target_encoding = data['encoding']

    if not isinstance(target_encoding, list) or len(target_encoding) != 128:
        return jsonify({'error': 'Invalid encoding format'}), 400

    threshold = data.get('threshold', 0.6)
    limit = data.get('limit', 20)

    subjects_with_faces = Subject.query.filter(
        Subject.face_encoding.isnot(None),
        Subject.is_deleted == False,
        Subject.photo_path.isnot(None)
    ).all()

    def euclidean_distance(enc1, enc2):
        import math
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(enc1, enc2)))

    matches = []
    for subject in subjects_with_faces:
        distance = euclidean_distance(target_encoding, subject.face_encoding)
        if distance < threshold:
            matches.append({
                'id': subject.id,
                'name': subject.name,
                'subject_type': subject.subject_type,
                'photo_path': subject.photo_path,
                'distance': round(distance, 4),
                'similarity': round((1 - distance) * 100, 1)
            })

    matches.sort(key=lambda x: x['distance'])
    matches = matches[:limit]

    return jsonify({
        'matches': matches,
        'total_searched': len(subjects_with_faces),
        'threshold': threshold
    })


@cms_bp.route('/api/subjects/with-faces', methods=['GET'])
@login_required
def get_subjects_with_faces():
    """Get list of subjects with face encodings for face-api.js matching."""
    subjects = Subject.query.filter(
        Subject.face_encoding.isnot(None),
        Subject.is_deleted == False,
        Subject.photo_path.isnot(None)
    ).all()

    return jsonify({
        'subjects': [{
            'id': s.id,
            'name': s.name,
            'photo_path': s.photo_path,
            'face_encoding': s.face_encoding
        } for s in subjects]
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
        related = Subject.query.filter(Subject.id.in_(
            related_ids), Subject.is_deleted == False).all() if related_ids else []

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

            second_degree_ids = [
                row.related_subject_id for row in second_degree_rows if row.related_subject_id != subject.id]
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
    try:
        subject = Subject.query.get_or_404(subject_id)
        data = request.get_json()
        if data is None:
            return jsonify({'error': 'Invalid JSON'}), 400

        related_id = data.get('related_subject_id')
        relationship_type = data.get('relationship_type', 'related')

        if not related_id:
            return jsonify({'error': 'Related subject ID required'}), 400

        if related_id == subject_id:
            return jsonify({'error': 'Cannot create relationship with self'}), 400

        related = Subject.query.get(related_id)
        if not related:
            return jsonify({'error': 'Related subject not found'}), 404

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
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding relationship: {e}")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/subjects/<subject_id>/remove-relationship', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def remove_subject_relationship(subject_id: str):
    """Remove a relationship between two subjects."""
    try:
        subject = Subject.query.get_or_404(subject_id)
        data = request.get_json()
        if data is None:
            return jsonify({'error': 'Invalid JSON'}), 400

        related_id = data.get('related_subject_id')

        if not related_id:
            return jsonify({'error': 'Related subject ID required'}), 400

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
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing relationship: {e}")
        return jsonify({'error': str(e)}), 500


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
        flash(
            f'Skipped {len(skipped)} subject(s) (already linked or not found).', 'warning')

    return redirect(url_for('cms.view_case', case_id=case_id))


@cms_bp.route('/subjects/<subject_id>/delete', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def delete_subject(subject_id: str):
    """Soft-delete a subject if not linked to any case."""
    subject = Subject.query.get_or_404(subject_id)

    # Check if subject is linked to any active case
    linked_cases = [c for c in Case.query.filter_by(
        is_deleted=False).all() if subject in c.subjects.all()]
    if linked_cases:
        case_list = ', '.join(
            [f'{c.case_number} ({c.title})' for c in linked_cases[:5]])
        extra = f' and {len(linked_cases)-5} more' if len(linked_cases) > 5 else ''
        return jsonify({
            'error': f'Kan subject niet verwijderen: gekoppeld aan {len(linked_cases)} za(a)k(en): {case_list}{extra}'
        }), 400

    subject.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Deleted subject: {subject.name}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({'message': 'Subject verwijderd'})
    flash(f'Subject {subject.name} is verwijderd.', 'info')
    return redirect(url_for('cms.subjects'))


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
        new_values={'amount': str(record.amount),
                    'date': str(record.transaction_date)},
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


@cms_bp.route('/api/findings/from-interpol', methods=['POST'])
@login_required
def create_findings_from_interpol():
    """Save Interpol/politie check results as findings."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    case_id = data.get('case_id')
    subject_id = data.get('subject_id')
    wanted = data.get('wanted_persons', [])
    missing = data.get('missing_persons', [])
    opsporingen = data.get('opsporingsberichten', [])

    if not case_id:
        return jsonify({'error': 'case_id is required'}), 400
    if not wanted and not missing and not opsporingen:
        return jsonify({'error': 'No results to save'}), 400

    case = Case.query.get(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404

    created = []
    for p in wanted:
        content_parts = [
            "Type: Red Notice (Wanted)",
        ]
        if p.get('date_of_birth'):
            content_parts.append(f"DOB: {p['date_of_birth']}")
        if p.get('nationality'):
            content_parts.append(f"Nationality: {p['nationality']}")
        if p.get('charge'):
            content_parts.append(f"Charge: {p['charge']}")
        if p.get('issuing_country'):
            content_parts.append(f"Issued by: {p['issuing_country']}")
        if p.get('url'):
            content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"INTERPOL Red Notice: {p.get('name', 'Unknown')}",
            content='\n'.join(content_parts),
            source_url=p.get('url', ''),
            source_type='interpol',
            finding_type='identity',
            reliability_score=7,
            confidence_level='medium',
            tags=['interpol', 'red_notice', 'wanted'],
            created_by=current_user.id
        )
        db.session.add(finding)
        created.append(finding)

    for p in missing:
        content_parts = [
            f"Type: {p.get('type', 'Missing Person')}",
        ]
        if p.get('date_of_birth'):
            content_parts.append(f"DOB: {p['date_of_birth']}")
        if p.get('nationality'):
            content_parts.append(f"Nationality: {p['nationality']}")
        if p.get('date_missing'):
            content_parts.append(f"Missing since: {p['date_missing']}")
        if p.get('place'):
            content_parts.append(f"Place: {p['place']}")
        if p.get('countries_likely_to_visit'):
            content_parts.append(
                f"Likely locations: {p['countries_likely_to_visit']}")
        if p.get('source') and p['source'] != 'INTERPOL':
            content_parts.append(f"Source: {p['source']}")
        if p.get('description'):
            content_parts.append(f"Info: {p['description']}")
        if p.get('url'):
            content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"INTERPOL / Vermist: {p.get('name', 'Unknown')}",
            content='\n'.join(content_parts),
            source_url=p.get('url', ''),
            source_type='interpol',
            finding_type='identity',
            reliability_score=7,
            confidence_level='medium',
            tags=['interpol', 'yellow_notice', 'missing'] if p.get(
                'source') == 'INTERPOL' else ['interpol', 'vermist', 'missing'],
            created_by=current_user.id
        )
        db.session.add(finding)
        created.append(finding)

    for p in opsporingen:
        content_parts = [
            "Type: Opsporingsbericht (Politie.nl)",
        ]
        if p.get('location'):
            content_parts.append(f"Locatie: {p['location']}")
        if p.get('date'):
            content_parts.append(f"Datum: {p['date']}")
        if p.get('url'):
            content_parts.append(f"URL: {p['url']}")

        finding = Finding(
            case_id=case_id,
            subject_id=subject_id,
            title=f"Opsporingsbericht: {p.get('title', 'Unknown')}",
            content='\n'.join(content_parts),
            source_url=p.get('url', ''),
            source_type='politie',
            finding_type='identity',
            reliability_score=6,
            confidence_level='medium',
            tags=['politie', 'opsporingsbericht', 'gezocht'],
            created_by=current_user.id
        )
        db.session.add(finding)
        created.append(finding)

    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='finding',
        entity_id=None,
        ip_address=request.remote_addr,
        case_id=case_id,
        new_values={'count': len(created), 'source': 'interpol_check'},
        description=f"Added {len(created)} Interpol findings to case {case.case_number}"
    )
    db.session.commit()

    return jsonify({
        'message': f'{len(created)} bevinding(en) opgeslagen',
        'count': len(created),
        'findings': [f.to_dict() for f in created]
    }), 201


# =============================================================================
# Search Routes
# =============================================================================

@cms_bp.route('/search')
@login_required
def search():
    """Global search across all entities with full page results."""
    query = request.args.get('q', '')
    entity_type = request.args.get('type', 'all')

    results = {
        'cases': [],
        'clients': [],
        'subjects': [],
        'findings': [],
        'financials': [],
        'comments': [],
        'notes': []
    }

    if query and len(query) >= 2:
        if entity_type in ['all', 'cases']:
            cases = Case.query.join(Client).filter(
                Case.is_deleted == False,
                db.or_(
                    Case.title.ilike(f'%{query}%'),
                    Case.case_number.ilike(f'%{query}%'),
                    Case.description.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['cases'] = [{
                'id': c.id,
                'title': c.title,
                'case_number': c.case_number,
                'status': c.status,
                'priority': c.priority,
                'client_name': c.client.name if c.client else None,
                'created_at': c.created_at.strftime('%Y-%m-%d') if c.created_at else None
            } for c in cases]

        if entity_type in ['all', 'clients']:
            clients = Client.query.filter(
                Client.is_deleted == False,
                Client.name.ilike(f'%{query}%')
            ).limit(20).all()
            results['clients'] = [{
                'id': c.id,
                'name': c.name,
                'contact_person': c.contact_person,
                'is_company': c.is_company,
                'is_active': c.is_active,
                'contract_number': c.contract_number
            } for c in clients]

        if entity_type in ['all', 'subjects']:
            subjects = Subject.query.filter(
                Subject.is_deleted == False,
                db.or_(
                    Subject.name.ilike(f'%{query}%'),
                    Subject.identification_number.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['subjects'] = [{
                'id': s.id,
                'name': s.name,
                'subject_type': s.subject_type,
                'risk_score': s.risk_score,
                'created_at': s.created_at.strftime('%Y-%m-%d') if s.created_at else None
            } for s in subjects]

        if entity_type in ['all', 'findings']:
            findings = Finding.query.join(Case).filter(
                Finding.is_deleted == False,
                db.or_(
                    Finding.title.ilike(f'%{query}%'),
                    Finding.content.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['findings'] = [{
                'id': f.id,
                'title': f.title,
                'case_id': f.case_id,
                'case_number': f.case.case_number if f.case else None,
                'finding_type': f.finding_type,
                'source_type': f.source_type,
                'created_at': f.created_at.strftime('%Y-%m-%d') if f.created_at else None
            } for f in findings]

        if entity_type in ['all', 'financials']:
            financials = FinancialRecord.query.join(Case).filter(
                FinancialRecord.is_deleted == False,
                db.or_(
                    FinancialRecord.description.ilike(f'%{query}%'),
                    FinancialRecord.source_reference.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['financials'] = [{
                'id': f.id,
                'amount': float(f.amount) if f.amount else 0,
                'currency': f.currency,
                'case_id': f.case_id,
                'case_number': f.case.case_number if f.case else None,
                'transaction_type': f.transaction_type,
                'transaction_date': f.transaction_date.strftime('%Y-%m-%d') if f.transaction_date else None,
                'description': f.description[:100] if f.description else None
            } for f in financials]

        if entity_type in ['all', 'comments']:
            comments = Comment.query.filter(
                Comment.is_deleted == False,
                Comment.content.ilike(f'%{query}%')
            ).limit(20).all()
            results['comments'] = [{
                'id': c.id,
                'content': c.content[:200] + ('...' if len(c.content) > 200 else ''),
                'comment_type': c.comment_type,
                'case_id': c.case_id,
                'subject_id': c.subject_id,
                'client_id': c.client_id,
                'case_number': Case.query.get(c.case_id).case_number if c.case_id else None,
                'author_name': c.author.full_name if c.author else 'Unknown',
                'created_at': c.created_at.strftime('%Y-%m-%d') if c.created_at else None
            } for c in comments]

        if entity_type in ['all', 'notes']:
            subject_notes = Subject.query.filter(
                Subject.is_deleted == False,
                Subject.notes.ilike(f'%{query}%')
            ).limit(10).all()
            results['notes'] = [{
                'id': s.id,
                'name': s.name,
                'subject_type': s.subject_type,
                'note_preview': s.notes[:150] + ('...' if len(s.notes) > 150 else '') if s.notes else None,
                'entity_type': 'subject'
            } for s in subject_notes]

        AuditLog.log(
            user_id=current_user.id,
            action='search',
            entity_type='global_search',
            ip_address=request.remote_addr,
            description=f"Searched for: {query}"
        )
        db.session.commit()

    return render_template('cms/search.html',
                           query=query,
                           results=results,
                           active_filter=entity_type
                           )


@cms_bp.route('/api/search')
@login_required
def api_search():
    """API endpoint for autocomplete/typeahead search."""
    query = request.args.get('q', '')
    entity_type = request.args.get('type', '')

    if not query or len(query) < 2:
        return jsonify({'results': []})

    results = {'cases': [], 'clients': [], 'subjects': []}

    if not entity_type or entity_type == 'cases':
        cases = Case.query.filter(
            Case.is_deleted == False,
            db.or_(
                Case.title.ilike(f'%{query}%'),
                Case.case_number.ilike(f'%{query}%')
            )
        ).limit(5).all()
        results['cases'] = [{
            'id': c.id,
            'title': c.title,
            'case_number': c.case_number,
            'type': 'case'
        } for c in cases]

    if not entity_type or entity_type == 'clients':
        clients = Client.query.filter(
            Client.is_deleted == False,
            Client.name.ilike(f'%{query}%')
        ).limit(5).all()
        results['clients'] = [{
            'id': c.id,
            'name': c.name,
            'type': 'client'
        } for c in clients]

    if not entity_type or entity_type == 'subjects':
        subjects = Subject.query.filter(
            Subject.is_deleted == False,
            Subject.name.ilike(f'%{query}%')
        ).limit(5).all()
        results['subjects'] = [{
            'id': s.id,
            'name': s.name,
            'type': 'subject',
            'subject_type': s.subject_type
        } for s in subjects]

    return jsonify({'results': results})


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
                           filters={'entity_type': entity_type, 'action': action,
                                    'user_id': user_id, 'case_id': case_id, 'search': search},
                           users=users,
                           entity_types=entity_types,
                           actions=actions
                           )


# =============================================================================
# Settings Routes
# =============================================================================

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
    setting = Setting.query.get_or_404(setting_id)
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
            setting = Setting.query.get(setting_id)
            if setting:
                old_value = setting.get_masked_value() if setting.is_sensitive else setting.value
                setting.value = new_value
                setting.updated_at = datetime.utcnow()

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
    except Exception:
        pass

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
    setting = Setting.query.get_or_404(setting_id)

    # Remove the setting (will be recreated by init_default_settings)
    setting.is_active = False
    setting.updated_at = datetime.utcnow()

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
        description="Deleted comment"
    )
    db.session.commit()

    return jsonify({'message': 'Comment deleted'})


@cms_bp.route('/api/comments/for-entity')
@login_required
def get_comments_for_entity():
    """Get all comments for a specific entity."""
    entity_type = request.args.get(
        'type')  # case, subject, client, financial_record
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

    comments = query.order_by(Comment.is_pinned.desc(),
                              Comment.created_at.desc()).all()

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


@cms_bp.route('/templates')
@login_required
def list_templates():
    """List all document templates."""
    templates = DocumentTemplate.query.filter_by(
        is_active=True).order_by(DocumentTemplate.name).all()
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

    flash('Template deleted.', 'success')
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

    templates = DocumentTemplate.query.filter_by(
        is_active=True).order_by(DocumentTemplate.name).all()

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

        flash('Report generated and saved.', 'success')
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
                # Map confidence_level to severity
                'severity': finding.confidence_level or 'medium',
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
    templates = DocumentTemplate.query.filter_by(
        is_active=True).order_by(DocumentTemplate.name).all()
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
    import app as flask_app
    with flask_app.app.app_context():
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
            if cancel_event and cancel_event.is_set():
                logger.info(f"OSINT search {search_id} was cancelled")
                search_manager.cleanup(search_id)
                return

            # Count results
            total_results = 0
            if results and 'categories' in results:
                for cat, items in results.get('categories', {}).items():
                    total_results += len(items) if items else 0

            # Persist to DB
            search_manager.set_results(search_id, results)
            logger.info(
                f"OSINT search {search_id} completed with {total_results} dork results, {len(results.get('search_links', []))} search links")

        except Exception as e:
            logger.error(f"OSINT search {search_id} failed: {str(e)}")
            logger.exception(e)
            search_manager.set_error(search_id, str(e))
        finally:
            # Cleanup cancel event after a delay
            def delayed_cleanup():
                import time
                time.sleep(300)
                search_manager.cleanup(search_id)

            cleanup_thread = threading.Thread(
                target=delayed_cleanup, daemon=True)
            cleanup_thread.start()


@cms_bp.route('/cases/<case_id>/osint-search', methods=['POST'])
@login_required
@case_access_required
def start_osint_search(case_id: str):
    """Start a background OSINT search for a person."""
    Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form

    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    if len(name.split()) < 2:
        return jsonify({'error': 'Please enter a full name (first and last name)'}), 400

    # Create search
    search_id = str(uuid.uuid4())
    search_manager.create_search(case_id, search_id, name)

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

    for result in selected_results:
        domain = result.get('domain', 'Unknown')
        query = result.get('query', '')
        source = result.get('source', '')
        category = result.get('category', 'general')

        if source == 'search_link':
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
        content_parts.append(
            f"Source: {source.upper() if source else 'Unknown'}")
        content_parts.append(f"URL: {result.get('url', 'N/A')}")
        content = '\n'.join(content_parts)

        # Build tags
        tags = ['osint', source.lower() if source else 'unknown']
        if category:
            tags.append(category.lower())
        if domain:
            # e.g., 'linkedin' from 'linkedin.com'
            tags.append(domain.split('.')[0])

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

from PIL import Image

UPLOAD_FOLDER = 'uploads'
SCREENSHOT_FOLDER = 'screenshots'


def get_screenshot_path(case_id: str, filename: str = None) -> str:
    """Get the path for screenshot storage."""
    base_path = os.path.join(current_app.root_path, 'static',
                             UPLOAD_FOLDER, 'cases', case_id, SCREENSHOT_FOLDER)
    if filename:
        return os.path.join(base_path, filename)
    return base_path


@cms_bp.route('/cases/<case_id>/screenshots')
@login_required
@case_access_required
def list_screenshots(case_id: str):
    """List all screenshots for a case."""
    Case.query.get_or_404(case_id)
    screenshots = Screenshot.query.filter_by(
        case_id=case_id).order_by(Screenshot.created_at.desc()).all()

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
    Case.query.get_or_404(case_id)

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

    # Get file extension from original filename or content type
    original_ext = file.filename.rsplit(
        '.', 1)[-1].lower() if '.' in file.filename else 'png'
    if original_ext not in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
        original_ext = 'png'

    filename = f"{screenshot_id}.{original_ext}"
    filepath = os.path.join(screenshot_dir, filename)

    # Initialize filepath to avoid unbound variable in except block
    filepath_defined = False

    try:
        # Read file content into memory first
        file_content = file.read()

        # Write to file
        with open(filepath, 'wb') as f:
            f.write(file_content)

        filepath_defined = True
        file_size = os.path.getsize(filepath)

        # Get URL from form
        url = request.form.get('url', '')

        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=url.split(
                '/')[-1][:300] if url else f'Screenshot {screenshot_id[:8]}',
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
        logger.error(f"Screenshot upload error: {e}")
        # Clean up file if it was created
        if filepath_defined and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Social Media ID Extraction Routes
# =============================================================================

import requests as http_requests


def _extract_social_ids_from_url(url, subject=None):
    """Extract social media IDs from a URL. Returns dict of extracted IDs.
    Optionally merges into subject.social_media_ids if subject is provided.
    Always adds username/platform from social-links when URL is a known social profile.
    """
    extracted = {}
    html = None

    # Pre-check: is this a social media URL?
    from .social_extractor import detect_platform, extract_username
    sl_platform = detect_platform(url)
    sl_username = extract_username(url, platform=sl_platform)

    # Skip scraping entirely for non-social URLs
    if not sl_platform and not sl_username:
        return extracted

    try:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(2000)
                html = page.content()
                browser.close()
            import socid_extractor
            extracted = socid_extractor.extract(html)
        except ImportError:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
            r = http_requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                html = r.text
                try:
                    import socid_extractor
                    extracted = socid_extractor.extract(html)
                except ImportError:
                    pass
        except Exception as e:
            logger.warning(f"Playwright extraction failed, trying HTTP: {e}")
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
            r = http_requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                html = r.text
                try:
                    import socid_extractor
                    extracted = socid_extractor.extract(html)
                except ImportError:
                    pass

        if not extracted and html:
            import re
            fb_p = [r'"entity_id":"(\d+)"', r'"profile_id":"(\d+)"', r'fb://page/\?id=(\d+)', r'&amp;id=(\d{10,})', r'"uid":(\d{10,})']
            for p in fb_p:
                m = re.findall(p, html)
                if m: extracted['facebook_id'] = m[0]; break
            ig_p = [r'"user_id":"(\d+)"', r'"id":"(\d{10,})"']
            for p in ig_p:
                m = re.findall(p, html)
                if m: extracted['instagram_id'] = m[0]; break
            tw_p = [r'data-user-id="(\d+)"', r'"rest_id":"(\d+)"', r'\"id_str\":\"(\d+)\"']
            for p in tw_p:
                m = re.findall(p, html)
                if m: extracted['twitter_id'] = m[0]; break
            tk_p = [r'\"id\":\"(\d+)\".*uniqueId', r'user-id="(\d+)"', r'"uid_(\d+)"']
            for p in tk_p:
                m = re.findall(p, html)
                if m: extracted['tiktok_id'] = m[0]; break
            rd_p = [r'"id":"(\w+)"', r'"name":"\w+","id":"(\w+)"']
            for p in rd_p:
                m = re.findall(p, html)
                if m: extracted['reddit_id'] = m[0]; break
    except Exception:
        pass

    # Always add social-links username when URL is a known social profile
    if sl_username and 'username' not in extracted:
        extracted['username'] = sl_username
    if not any(k.endswith('id') for k in extracted):
        extracted['source_platform'] = sl_platform or 'unknown'

    if subject:
        existing = subject.social_media_ids or {}
        for key, value in extracted.items():
            if key in ['links', 'created_at', 'updated_at', 'fullname', 'tagline']:
                continue
            if key.endswith('id') or key in ['facebook', 'vk', 'instagram', 'twitter', 'tiktok', 'linkedin', 'reddit', 'platform', 'username', 'source_platform']:
                existing[key] = value
        subject.social_media_ids = existing
        db.session.commit()

    return extracted


@cms_bp.route('/extract-social-id', methods=['POST'])
@login_required
def extract_social_id():
    """
    Extract social media IDs from a URL using socid_extractor.
    Uses Playwright for JS-heavy sites.
    """
    data = request.get_json()
    if not data or not data.get('url'):
        return jsonify({'error': 'URL is required'}), 400

    url = data.get('url')
    subject_id = data.get('subject_id')
    subject = Subject.query.get(subject_id) if subject_id else None

    extracted = _extract_social_ids_from_url(url, subject=subject)

    # Always try social-links as fallback for platform + username
    from .social_extractor import detect_platform, extract_username
    sl_platform = detect_platform(url)
    sl_username = extract_username(url, platform=sl_platform)

    if not extracted and (sl_platform or sl_username):
        # Save social-links results even without socid_extractor IDs
        extracted = {}
        if sl_username:
            extracted['username'] = sl_username
        if sl_platform:
            extracted['profile_platform'] = sl_platform
        if subject:
            existing = subject.social_media_ids or {}
            if sl_username:
                existing['username'] = sl_username
            if sl_platform:
                existing['profile_platform'] = sl_platform
            subject.social_media_ids = existing
            db.session.commit()

    if not extracted:
        return jsonify({
            'message': 'No social media IDs found on this page',
            'url': url,
            'extracted': {},
            'platform': sl_platform,
            'username': sl_username,
            'note': 'Some sites (Facebook, Instagram) block automated access. Try manual extraction.'
        }), 200

    if subject:
        return jsonify({
            'message': 'Social media IDs extracted and saved',
            'url': url,
            'extracted': extracted,
            'saved_to_subject': True,
            'subject_id': subject_id,
            'platform': sl_platform,
            'username': sl_username,
        }), 200

    return jsonify({
        'message': 'Social media IDs extracted',
        'url': url,
        'extracted': extracted,
        'platform': sl_platform,
        'username': sl_username,
    }), 200


@cms_bp.route('/subjects/<subject_id>/social-ids', methods=['GET'])
@login_required
def get_subject_social_ids(subject_id: str):
    """Get social media IDs for a subject."""
    subject = Subject.query.get_or_404(subject_id)

    return jsonify({
        'subject_id': subject_id,
        'social_media_ids': subject.social_media_ids or {}
    })


@cms_bp.route('/subjects/<subject_id>/social-ids', methods=['PUT'])
@login_required
def update_subject_social_ids(subject_id: str):
    """Update social media IDs for a subject (manual entry)."""
    subject = Subject.query.get_or_404(subject_id)
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Merge with existing
    existing = subject.social_media_ids or {}
    new_data = data.get('social_media_ids', {})

    for platform, info in new_data.items():
        existing[platform] = info

    subject.social_media_ids = existing
    db.session.commit()

    return jsonify({
        'message': 'Social media IDs updated',
        'social_media_ids': subject.social_media_ids
    })


# =============================================================================
# Social Account Routes (searchable usernames per subject)
# =============================================================================

@cms_bp.route('/api/subjects/<subject_id>/social-accounts', methods=['POST'])
@login_required
def add_social_account(subject_id: str):
    """Add a social account (username) to a subject."""
    from .models import SocialAccount
    subject = Subject.query.get_or_404(subject_id)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    platform = (data.get('platform') or '').strip().lower()
    username = (data.get('username') or '').strip()
    if not platform or not username:
        return jsonify({'error': 'platform and username required'}), 400
    account = SocialAccount(
        subject_id=subject.id,
        platform=platform,
        username=username,
        url=(data.get('url') or '').strip(),
        account_id=(data.get('account_id') or '').strip(),
    )
    db.session.add(account)
    db.session.commit()
    return jsonify({'message': 'Social account added', 'account': account.to_dict()}), 201


@cms_bp.route('/api/subjects/<subject_id>/social-accounts/<account_id>', methods=['DELETE'])
@login_required
def delete_social_account(subject_id: str, account_id: str):
    """Delete a social account."""
    from .models import SocialAccount
    account = SocialAccount.query.filter_by(id=account_id, subject_id=subject_id).first_or_404()
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'Social account deleted'})


@cms_bp.route('/api/subjects/create-from-username', methods=['POST'])
@login_required
def create_subject_from_username():
    """Create a subject from just a username (no full name needed)."""
    from .models import SocialAccount
    from .social_extractor import detect_platform, extract_username
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    username = (data.get('username') or '').strip()
    platform = (data.get('platform') or '').strip().lower()
    url = (data.get('url') or '').strip()

    if not username:
        if url:
            username = extract_username(url) or ''
        if not username:
            return jsonify({'error': 'username required'}), 400

    if not platform:
        if url:
            platform = detect_platform(url) or ''
        if not platform:
            platform = 'other'

    display_name = f"{username} ({platform})" if platform != 'other' else username
    subject = Subject(
        name=display_name,
        subject_type='person',
        notes=f"Created from username '{username}' on {platform}",
    )
    db.session.add(subject)
    db.session.flush()

    account = SocialAccount(
        subject_id=subject.id,
        platform=platform,
        username=username,
        url=url,
    )
    db.session.add(account)

    # Optionally link to a case
    case_id = data.get('case_id')
    if case_id:
        from .models import Case
        case = Case.query.get(case_id)
        if case:
            subject.cases.append(case)

    db.session.commit()
    return jsonify({'message': 'Subject created', 'subject': subject.to_dict(), 'account': account.to_dict()}), 201


@cms_bp.route('/api/findings/save-as-social-account', methods=['POST'])
@login_required
def save_finding_as_social_account():
    """Save an OSINT finding's source URL as a social account on the linked subject."""
    from .models import SocialAccount
    from .social_extractor import detect_platform, extract_username
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    finding_id = data.get('finding_id')
    subject_id = data.get('subject_id')

    if not finding_id and not subject_id:
        return jsonify({'error': 'finding_id or subject_id required'}), 400

    url = data.get('url') or ''
    platform = (data.get('platform') or '').strip().lower()
    username = (data.get('username') or '').strip()

    if finding_id:
        from .models import Finding
        finding = Finding.query.get_or_404(finding_id)
        if not finding.subject_id:
            return jsonify({'error': 'Finding not linked to a subject'}), 400
        subject_id = finding.subject_id
        if not url:
            url = finding.source_url
        if not username:
            from .models import Subject
            subj = Subject.query.get(subject_id)
            username = data.get('username') or (subj.name if subj else finding.title.strip())

    # Smart detection via social-links, fall back to naive
    if not username and url:
        username = extract_username(url, platform=platform)
    if not username and url:
        import re
        path = re.sub(r'https?://', '', url).split('/')
        username = path[-1] if len(path) > 1 else path[0]
    if not username:
        username = url or 'unknown'

    if not platform and url:
        platform = detect_platform(url)
    if not platform:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        platform = parsed.netloc.replace('www.', '').split('.')[0] if parsed.netloc else 'unknown'

    account = SocialAccount(
        subject_id=subject_id,
        platform=platform,
        username=username,
        url=url,
    )
    db.session.add(account)
    db.session.commit()
    return jsonify({'message': 'Social account created', 'account': account.to_dict()}), 201


@cms_bp.route('/subjects/<subject_id>/bulk-extract-social-ids', methods=['POST'])
@login_required
def bulk_extract_social_ids(subject_id: str):
    """Extract social media IDs from all findings linked to a subject."""
    from .models import Finding
    from .social_extractor import detect_platform
    subject = Subject.query.get_or_404(subject_id)
    findings = Finding.query.filter_by(subject_id=subject_id).filter(Finding.source_url.isnot(None)).filter(Finding.source_url != '').all()

    if not findings:
        return jsonify({'message': 'No findings with URLs to scan', 'found': 0, 'total': 0}), 200

    total_found = 0
    skipped = 0
    not_social = 0

    for finding in findings:
        url = finding.source_url
        if not detect_platform(url):
            not_social += 1
            continue
        try:
            result = _extract_social_ids_from_url(url, subject=subject)
            if result:
                total_found += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1

    parts = [f'Scanned {len(findings)} findings.']
    if total_found:
        parts.append(f'IDs saved from {total_found} social profile(s).')
    if not_social:
        parts.append(f'{not_social} non-social URL(s) skipped.')
    if skipped:
        parts.append(f'{skipped} social URL(s) yielded no IDs.')

    return jsonify({
        'message': ' '.join(parts),
        'found': total_found,
        'total': len(findings),
        'skipped': skipped,
        'not_social': not_social,
    }), 200


# =============================================================================
# Politie Open Data Routes
# =============================================================================

_INTERPOL_USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0',
]

_LAST_INTERPOL_CALL = 0
_INTERPOL_LOCK = threading.Lock()
_INTERPOL_MIN_INTERVAL = 30  # seconds between requests to avoid Akamai rate limiting


def _interpol_headers():
    return {
        'User-Agent': random.choice(_INTERPOL_USER_AGENTS),
        'Accept': 'application/json'
    }


def _check_interpol_rate_limit():
    global _LAST_INTERPOL_CALL
    with _INTERPOL_LOCK:
        now = time.time()
        elapsed = now - _LAST_INTERPOL_CALL
        if elapsed < _INTERPOL_MIN_INTERVAL:
            return _INTERPOL_MIN_INTERVAL - elapsed
        _LAST_INTERPOL_CALL = now
        return 0


@cms_bp.route('/check-policie-data', methods=['POST'])
@login_required
def check_policie_data():
    """
    Check subject against INTERPOL Red Notices (wanted) and Yellow Notices (missing).
    Also checks politie.nl/vermist for Dutch missing persons.
    """
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    subject_name = data.get('name', '').strip()
    subject_id = data.get('subject_id')

    if not subject_name:
        return jsonify({'error': 'Subject name is required'}), 400

    name_parts = subject_name.lower().split()
    forename = name_parts[0] if len(name_parts) > 0 else ''
    surname = name_parts[-1] if len(name_parts) > 1 else ''

    results = {
        'subject_name': subject_name,
        'subject_id': subject_id,
        'missing_persons': [],
        'wanted_persons': [],
        'opsporingsberichten': [],
        'api_available': True,
        'source': 'interpol',
        'error': None
    }

    # Rate limit check
    wait = _check_interpol_rate_limit()
    if wait > 0:
        logger.warning(f"Interpol rate limited: retry in {wait:.0f}s")
        return jsonify({
            'subject_name': subject_name,
            'subject_id': subject_id,
            'missing_persons': [],
            'wanted_persons': [],
            'api_available': False,
            'source': 'interpol',
            'error': f'Interpol API rate limit: wacht {wait:.0f} seconden voor volgende aanvraag',
            'retry_after': int(wait)
        }), 429

    interpol_403 = False
    try:
        import httpx
        client = httpx.Client(headers=_interpol_headers(), timeout=15)

        # --- Interpol Red Notices (wanted) ---
        try:
            red_params = {'resultPerPage': 10}
            if surname:
                red_params['name'] = surname
            if forename:
                red_params['forename'] = forename
            r = client.get(
                'https://ws-public.interpol.int/notices/v1/red', params=red_params)
            if r.status_code == 200:
                red_data = r.json()
                for notice in red_data.get('_embedded', {}).get('notices', []):
                    nid = notice['entity_id'].replace('/', '-')
                    # Get detail for charges
                    detail = None
                    try:
                        dr = client.get(
                            f'https://ws-public.interpol.int/notices/v1/red/{nid}')
                        if dr.status_code == 200:
                            detail = dr.json()
                    except Exception:
                        pass
                    charge = ''
                    issuing = ''
                    if detail and detail.get('arrest_warrants'):
                        aw = detail['arrest_warrants'][0]
                        charge = aw.get('charge', '')
                        issuing = aw.get('issuing_country_id', '')
                    results['wanted_persons'].append({
                        'name': f"{notice.get('forename', '')} {notice.get('name', '')}".strip(),
                        'forename': notice.get('forename', ''),
                        'surname': notice.get('name', ''),
                        'date_of_birth': notice.get('date_of_birth', ''),
                        'nationality': ', '.join(notice.get('nationalities', [])),
                        'charge': charge,
                        'issuing_country': issuing,
                        'url': notice.get('_links', {}).get('self', {}).get('href', ''),
                        'thumbnail': notice.get('_links', {}).get('thumbnail', {}).get('href', ''),
                        'type': 'Red Notice (Wanted)',
                        'source': 'INTERPOL'
                    })
            elif r.status_code == 403:
                interpol_403 = True
                logger.warning(
                    "Interpol Red Notice 403 Forbidden (Akamai block)")
        except Exception as e:
            logger.warning(f"Interpol Red Notice lookup error: {e}")

        # --- Interpol Yellow Notices (missing) ---
        try:
            yellow_params = {'resultPerPage': 10}
            if surname:
                yellow_params['name'] = surname
            if forename:
                yellow_params['forename'] = forename
            r = client.get(
                'https://ws-public.interpol.int/notices/v1/yellow', params=yellow_params)
            if r.status_code == 200:
                yellow_data = r.json()
                for notice in yellow_data.get('_embedded', {}).get('notices', []):
                    nid = notice['entity_id'].replace('/', '-')
                    detail = None
                    try:
                        dr = client.get(
                            f'https://ws-public.interpol.int/notices/v1/yellow/{nid}')
                        if dr.status_code == 200:
                            detail = dr.json()
                    except Exception:
                        pass
                    results['missing_persons'].append({
                        'name': f"{notice.get('forename', '')} {notice.get('name', '')}".strip(),
                        'forename': notice.get('forename', ''),
                        'surname': notice.get('name', ''),
                        'date_of_birth': notice.get('date_of_birth', ''),
                        'nationality': ', '.join(notice.get('nationalities', [])),
                        'date_missing': detail.get('date_of_event', '') if detail else '',
                        'place': detail.get('place', '') if detail else '',
                        'countries_likely_to_visit': ', '.join(detail.get('countries_likely_to_be_visited', [])) if detail else '',
                        'url': notice.get('_links', {}).get('self', {}).get('href', ''),
                        'thumbnail': notice.get('_links', {}).get('thumbnail', {}).get('href', ''),
                        'type': 'Yellow Notice (Missing)',
                        'source': 'INTERPOL'
                    })
            elif r.status_code == 403:
                interpol_403 = True
                logger.warning(
                    "Interpol Yellow Notice 403 Forbidden (Akamai block)")
        except Exception as e:
            logger.warning(f"Interpol Yellow Notice lookup error: {e}")

        # If Interpol returned no results (or was blocked), try politie.nl/vermist fallback
        if (len(results['wanted_persons']) == 0 and len(results['missing_persons']) == 0
                and len(name_parts) >= 1):
            try:
                vermist_resp = httpx.get('https://www.politie.nl/vermist',
                                         headers=_interpol_headers(), timeout=10, follow_redirects=True)
                if vermist_resp.status_code == 200:
                    import re as re2
                    case_links = re2.findall(
                        r'href="(/vermist/[^"]+)"', vermist_resp.text)
                    for link in case_links[:20]:
                        try:
                            detail = httpx.get(f'https://www.politie.nl{link}',
                                               headers=_interpol_headers(), timeout=10, follow_redirects=True)
                            if detail.status_code == 200:
                                text_lower = detail.text.lower()
                                if any(part in text_lower for part in name_parts):
                                    title_match = re2.search(
                                        r'<h1[^>]*>([^<]+)</h1>', detail.text)
                                    title = title_match.group(
                                        1).strip() if title_match else 'Unknown'
                                    results['missing_persons'].append({
                                        'name': title,
                                        'source': 'politie.nl/vermist',
                                        'url': f'https://www.politie.nl{link}',
                                        'type': 'Missing Person (Netherlands)',
                                        'description': 'Matching name parts found on politie.nl'
                                    })
                        except Exception:
                            pass
            except Exception:
                pass

        results['api_available'] = not interpol_403
        if interpol_403 and len(results['wanted_persons']) == 0 and len(results['missing_persons']) == 0:
            results['error'] = 'INTERPOL API is tijdelijk geblokkeerd (Akamai). Politie.nl check uitgevoerd als fallback.'
            results['source'] = 'politie.nl (fallback)'
            logger.warning(
                f"Interpol blocked (403), fell back to politie.nl for {subject_name}")
        else:
            logger.info(
                f"Check for {subject_name}: {len(results['wanted_persons'])} wanted, {len(results['missing_persons'])} missing")

        # Check politie.nl/gezocht for Dutch wanted bulletins
        try:
            from cms.politie_scraper import search_opsporingsberichten
            gezocht = search_opsporingsberichten(
                forename=forename, surname=surname, max_pages=2)
            results['opsporingsberichten'] = gezocht.get('matches', [])
            if gezocht['match_count'] > 0:
                logger.info(
                    f"Found {gezocht['match_count']} opsporingsberichten for {subject_name}")
        except Exception as e:
            logger.warning(f"Opsporingsberichten check error: {e}")

        return jsonify(results), 200

    except Exception as e:
        logger.error(f"Interpol data check error: {e}")
        return jsonify({
            'error': f'Failed to check data: {str(e)}',
            'api_available': False
        }), 500


@cms_bp.route('/check-policie-data-status', methods=['GET'])
@login_required
def check_policie_api_status():
    """Check if INTERPOL API is available."""
    wait = _check_interpol_rate_limit()
    if wait > 0:
        return jsonify({
            'available': False,
            'status_code': 429,
            'error': f'Rate limited, retry in {wait:.0f}s',
            'retry_after': int(wait)
        }), 200
    try:
        import httpx
        r = httpx.get('https://ws-public.interpol.int/notices/v1/red',
                      params={'resultPerPage': 1},
                      headers=_interpol_headers(),
                      timeout=10)
        return jsonify({
            'available': r.status_code == 200,
            'status_code': r.status_code,
            'api_url': 'https://ws-public.interpol.int/notices/v1/',
            'source': 'INTERPOL'
        }), 200
    except Exception as e:
        return jsonify({
            'available': False,
            'error': str(e)
        }), 200


# =============================================================================
# RDW Vehicle Data Routes
# =============================================================================

RDW_API_BASE = 'https://opendata.rdw.nl/resource/m9d7-ebf2.json'


def normalize_kenteken(kenteken: str) -> str:
    """Normalize kenteken format (remove spaces, dashes, uppercase)."""
    return kenteken.upper().replace('-', '').replace(' ', '')


def denormalize_kenteken(kenteken: str) -> str:
    """Add dashes to kenteken for display (e.g., 22PBR2 -> 22-PBR-2)."""
    kenteken = kenteken.upper().replace('-', '').replace(' ', '')
    if len(kenteken) == 6:
        return f"{kenteken[:2]}-{kenteken[2:5]}-{kenteken[5:]}"
    elif len(kenteken) == 5:
        return f"{kenteken[:2]}-{kenteken[2:4]}-{kenteken[4:]}"
    return kenteken


@cms_bp.route('/check-rdw-vehicle', methods=['POST'])
@login_required
def check_rdw_vehicle():
    """
    Check vehicle data from RDW (Dutch Road Transport Authority).
    Returns vehicle details based on license plate (kenteken).
    """
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    kenteken = data.get('kenteken', '').strip()
    subject_id = data.get('subject_id')

    if not kenteken:
        return jsonify({'error': 'Kenteken (license plate) is required'}), 400

    # Normalize kenteken
    kenteken_normalized = normalize_kenteken(kenteken)

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; OSINT-CMS/1.0)',
            'Accept': 'application/json'
        }

        # Query RDW API
        url = f'{RDW_API_BASE}?kenteken={kenteken_normalized}'
        r = http_requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            return jsonify({
                'error': f'RDW API returned status {r.status_code}',
                'kenteken': kenteken_normalized
            }), 502

        results = r.json()

        if not results:
            return jsonify({
                'found': False,
                'kenteken': kenteken_normalized,
                'kenteken_display': denormalize_kenteken(kenteken_normalized),
                'message': 'No vehicle found for this license plate'
            }), 200

        vehicle = results[0]

        # Build response with relevant fields
        vehicle_data = {
            'found': True,
            'kenteken': vehicle.get('kenteken', ''),
            'kenteken_display': denormalize_kenteken(vehicle.get('kenteken', '')),
            'voertuigsoort': vehicle.get('voertuigsoort', ''),
            'merk': vehicle.get('merk', ''),
            'handelsbenaming': vehicle.get('handelsbenaming', ''),
            'inrichting': vehicle.get('inrichting', ''),
            'type': vehicle.get('type', ''),
            'variant': vehicle.get('variant', ''),
            'uitvoering': vehicle.get('uitvoering', ''),
            'kleur': vehicle.get('eerste_kleur', ''),
            'tweede_kleur': vehicle.get('tweede_kleur', ''),
            'aantal_deuren': vehicle.get('aantal_deuren', ''),
            'aantal_zitplaatsen': vehicle.get('aantal_zitplaatsen', ''),
            'cilinderinhoud': vehicle.get('cilinderinhoud', ''),
            'aantal_cilinders': vehicle.get('aantal_cilinders', ''),
            'vermogen': vehicle.get('vermogen_massarijklaar', ''),
            'massa_ledig': vehicle.get('massa_ledig_voertuig', ''),
            'maximum_massa': vehicle.get('toegestane_maximum_massa_voertuig', ''),
            'wielbasis': vehicle.get('wielbasis', ''),
            'datum_eerste_toelating': vehicle.get('datum_eerste_toelating', ''),
            'datum_tenaamstelling': vehicle.get('datum_tenaamstelling', ''),
            'vervaldatum_apk': vehicle.get('vervaldatum_apk', ''),
            'europese_voertuigcategorie': vehicle.get('europese_voertuigcategorie', ''),
            'wam_verzekerd': vehicle.get('wam_verzekerd', ''),
            'taxi_indicator': vehicle.get('taxi_indicator', ''),
            'export_indicator': vehicle.get('export_indicator', ''),
            'zuinigheidsclassificatie': vehicle.get('zuinigheidsclassificatie', ''),
            'catalogusprijs': vehicle.get('catalogusprijs', ''),
            'bruto_bpm': vehicle.get('bruto_bpm', ''),
            'openstaande_terugroepactie': vehicle.get('openstaande_terugroepactie_indicator', ''),
            'typegoedkeuringsnummer': vehicle.get('typegoedkeuringsnummer', ''),
        }

        # If subject_id provided, suggest updating
        if subject_id:
            vehicle_data['subject_id'] = subject_id
            vehicle_data['suggested_update'] = {
                'brand': vehicle.get('merk', ''),
                'vehicle_type': vehicle.get('inrichting', ''),
                'notes': f"RDW Data: {vehicle.get('merk', '')} {vehicle.get('handelsbenaming', '')} ({denormalize_kenteken(vehicle.get('kenteken', ''))})"
            }

        return jsonify(vehicle_data), 200

    except http_requests.exceptions.RequestException as e:
        logger.error(f"RDW API error: {e}")
        return jsonify({
            'error': f'Failed to connect to RDW API: {str(e)}'
        }), 503

    except Exception as e:
        logger.error(f"RDW check error: {e}")
        return jsonify({
            'error': f'Failed to check RDW data: {str(e)}'
        }), 500


@cms_bp.route('/subjects/<subject_id>/update-from-rdw', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def update_subject_from_rdw(subject_id: str):
    """Update vehicle subject fields with data from RDW."""
    subject = Subject.query.get_or_404(subject_id)

    if subject.subject_type != 'vehicle':
        return jsonify({'error': 'Subject is not a vehicle'}), 400

    data = request.get_json()

    if not data or not data.get('kenteken'):
        return jsonify({'error': 'Kenteken is required'}), 400

    kenteken = normalize_kenteken(data.get('kenteken'))

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; OSINT-CMS/1.0)',
            'Accept': 'application/json'
        }

        url = f'{RDW_API_BASE}?kenteken={kenteken}'
        r = http_requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200 or not r.json():
            return jsonify({'error': 'Vehicle not found in RDW database'}), 404

        vehicle = r.json()[0]

        # Update subject fields
        if vehicle.get('merk'):
            subject.brand = vehicle.get('merk')

        if vehicle.get('inrichting'):
            subject.vehicle_type = vehicle.get('inrichting')

        # Build notes with RDW data
        rdw_notes = []
        rdw_notes.append(f"Kenteken: {denormalize_kenteken(kenteken)}")
        if vehicle.get('merk'):
            rdw_notes.append(f"Merk: {vehicle.get('merk')}")
        if vehicle.get('handelsbenaming'):
            rdw_notes.append(f"Model: {vehicle.get('handelsbenaming')}")
        if vehicle.get('voertuigsoort'):
            rdw_notes.append(f"Type: {vehicle.get('voertuigsoort')}")
        if vehicle.get('inrichting'):
            rdw_notes.append(f"Inrichting: {vehicle.get('inrichting')}")
        if vehicle.get('kleur'):
            rdw_notes.append(f"Kleur: {vehicle.get('eerste_kleur')}")
        if vehicle.get('vervaldatum_apk'):
            rdw_notes.append(
                f"APK vervaldatum: {vehicle.get('vervaldatum_apk')}")
        if vehicle.get('wam_verzekerd'):
            rdw_notes.append(
                f"Verzekerd (WAM): {vehicle.get('wam_verzekerd')}")

        existing_notes = subject.notes or ''
        new_notes = '[RDW Data]\n' + '\n'.join(rdw_notes)
        subject.notes = new_notes + '\n\n' + \
            existing_notes if existing_notes else new_notes

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='subject',
            entity_id=subject_id,
            ip_address=request.remote_addr,
            description=f"Updated vehicle data from RDW for: {denormalize_kenteken(kenteken)}"
        )

        db.session.commit()

        return jsonify({
            'message': 'Subject updated from RDW data',
            'subject': subject.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"RDW update error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# =========================================================================
# Vessel Lookup Endpoints
# =========================================================================


@cms_bp.route('/api/vessel-lookup', methods=['POST'])
@login_required
def vessel_lookup():
    """Look up vessel data from MarinePlan, KVNR, Binnenvaart.eu, and optionally Equasis.

    Accepts: {subject_id, name, imo, mmsi, eni}
    Returns merged vessel data from all available sources.
    """
    if not VESSEL_SERVICE_AVAILABLE:
        return jsonify({'error': 'Vessel service not available'}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        name = (data.get('name') or '').strip()
        imo = (data.get('imo') or '').strip()
        mmsi = (data.get('mmsi') or '').strip()
        eni = (data.get('eni') or '').strip()

        if not name and not imo and not mmsi and not eni:
            return jsonify({'error': 'Provide at least name, IMO, MMSI, or ENI'}), 400

        result = lookup_vessel(imo=imo or None, mmsi=mmsi or None,
                               eni=eni or None, name=name or None)

        subject_id = data.get('subject_id')
        if subject_id and result.get('found'):
            subject = Subject.query.get(subject_id)
            if subject:
                result['suggested_update'] = {
                    'imo_number': result.get('imo'),
                    'mmsi': result.get('mmsi'),
                    'eni_number': result.get('eni'),
                    'vessel_nationality': result.get('flag'),
                    'vessel_data': result.get('source_data')
                }

        return jsonify(result), 200

    except Exception as e:
        logger.exception(f"Vessel lookup error: {e}")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/api/vessel/update-subject', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def update_subject_from_vessel():
    """Update subject with vessel data from lookup."""
    data = request.get_json()
    if not data or not data.get('subject_id'):
        return jsonify({'error': 'subject_id is required'}), 400

    subject = Subject.query.get_or_404(data['subject_id'])
    if subject.subject_type != 'vessel':
        return jsonify({'error': 'Subject is not a vessel'}), 400

    changes = {}

    vessel_fields = ['imo_number', 'mmsi', 'eni_number', 'vessel_nationality']
    for field in vessel_fields:
        if data.get(field):
            setattr(subject, field, encryptor.encrypt(str(data[field])))
            changes[field] = {'old': 'updated', 'new': str(data[field])}

    if data.get('vessel_data'):
        vd = data['vessel_data']
        # Ensure it's a dict (JSON column handles serialization)
        if isinstance(vd, str):
            try:
                vd = json.loads(vd)
            except json.JSONDecodeError:
                pass
        subject.vessel_data = vd if isinstance(vd, dict) else {}
        changes['vessel_data'] = {
            'old': 'updated', 'new': 'Vessel data updated'}

    subject.updated_at = datetime.utcnow()

    AuditLog.log(
        user_id=current_user.id,
        action='update',
        entity_type='subject',
        entity_id=subject.id,
        changes=changes,
        ip_address=request.remote_addr,
        description=f"Updated vessel subject: {subject.name}"
    )
    db.session.commit()

    return jsonify({'message': 'Vessel subject updated', 'subject': subject.to_dict()}), 200


@cms_bp.route('/api/findings/from-vessel', methods=['POST'])
@login_required
def create_finding_from_vessel():
    """Create a Finding from vessel lookup data.

    Accepts: {case_id, subject_id, vessel_data, source}
    """
    data = request.get_json()
    case_id = data.get('case_id')
    subject_id = data.get('subject_id')

    if not case_id:
        return jsonify({'error': 'case_id is required'}), 400

    vessel_info = data.get('vessel_data', {})
    source = data.get('source', 'vessel_lookup')

    if not vessel_info or not isinstance(vessel_info, dict):
        return jsonify({'error': 'vessel_data is required'}), 400

    # Build content from vessel data
    content_parts = ['Vessel Lookup Results', '=' * 30]
    name = vessel_info.get('name') or 'Unknown'
    content_parts.append(f"Name: {name}")
    content_parts.append(f"IMO: {vessel_info.get('imo', 'N/A')}")
    content_parts.append(f"MMSI: {vessel_info.get('mmsi', 'N/A')}")
    content_parts.append(f"ENI: {vessel_info.get('eni', 'N/A')}")
    content_parts.append(f"Flag: {vessel_info.get('flag', 'N/A')}")
    content_parts.append(f"Ship Type: {vessel_info.get('ship_type', 'N/A')}")
    content_parts.append(f"Length: {vessel_info.get('length', 'N/A')}")
    content_parts.append(f"Beam: {vessel_info.get('beam', 'N/A')}")
    content_parts.append(f"Year Built: {vessel_info.get('year_built', 'N/A')}")
    content_parts.append(f"Callsign: {vessel_info.get('callsign', 'N/A')}")
    content_parts.append(
        f"Destination: {vessel_info.get('destination', 'N/A')}")

    pos = vessel_info.get('position')
    if pos:
        content_parts.append(
            f"Position: {pos.get('lat', '?')}, {pos.get('lon', '?')}")
    if vessel_info.get('speed'):
        content_parts.append(f"Speed: {vessel_info['speed']} km/h")
    if vessel_info.get('builder'):
        content_parts.append(f"Builder: {vessel_info['builder']}")

    sources = vessel_info.get('sources', [])
    content_parts.append(f"\nSources: {', '.join(sources)}")

    sources_data = vessel_info.get('source_data', {})
    if sources_data.get('vesselfinder'):
        vf = sources_data['vesselfinder']
        content_parts.append(f"\nVesselFinder: {vf.get('source_url', '')}")
    if sources_data.get('marineplan'):
        mp = sources_data['marineplan']
        content_parts.append(f"\nMarinePlan: {mp.get('source_url', '')}")
    if sources_data.get('kvnr'):
        kvnr = sources_data['kvnr']
        content_parts.append(f"KVNR: {kvnr.get('source_url', '')}")
    if sources_data.get('binnenvaart'):
        bv = sources_data['binnenvaart']
        content_parts.append(f"Binnenvaart.eu: {bv.get('source_url', '')}")
    if sources_data.get('equasis'):
        eq = sources_data['equasis']
        content_parts.append(f"Equasis: {eq.get('source_url', '')}")

    title = f"Vessel Check: {name}"

    finding = Finding(
        case_id=case_id,
        subject_id=subject_id,
        title=title[:300],
        content='\n'.join(content_parts),
        source_url=data.get('source_url', ''),
        source_type=source,
        finding_type='vessel',
        reliability_score=6,
        confidence_level='medium',
        tags=['vessel', source] + \
            [f'imo:{vessel_info.get("imo")}'] if vessel_info.get(
                'imo') else ['vessel', source],
        created_by=current_user.id
    )
    db.session.add(finding)

    AuditLog.log(
        user_id=current_user.id,
        action='create',
        entity_type='finding',
        entity_id=finding.id,
        new_values={'title': finding.title, 'source_type': source},
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Created vessel finding: {finding.title}"
    )
    db.session.commit()

    return jsonify({
        'message': f'Bevinding opgeslagen: {title}',
        'finding': finding.to_dict()
    }), 201


@cms_bp.route('/api/kadaster-lookup', methods=['POST'])
@login_required
def kadaster_lookup():
    """
    Look up a Dutch address in the BAG (Basisregistratie Adressen) via PDOK API.
    
    Accepts: {street, number, zipcode, town} or a full query string.
    Returns verified address data from the Dutch cadastre.
    """
    data = request.get_json() if request.is_json else request.form

    query = data.get('query', '')
    if not query:
        parts = []
        if data.get('street'):
            parts.append(data['street'])
        if data.get('number'):
            parts.append(data['number'])
        if data.get('zipcode'):
            parts.append(data['zipcode'])
        if data.get('town'):
            parts.append(data['town'])
        query = ' '.join(parts)

    if not query:
        return jsonify({'error': 'No address provided'}), 400

    try:
        import httpx
        pdok_url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
        params = {'q': query, 'rows': 1, 'fl': '*'}

        resp = httpx.get(pdok_url, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json()

        docs = result.get('response', {}).get('docs', [])
        if not docs:
            logger.warning(f"Kadaster lookup not found: {query}")
            return jsonify({
                'found': False,
                'message': 'Address not found in BAG registry',
                'query': query
            }), 200

        doc = docs[0]
        logger.info(
            f"Kadaster lookup OK: {query} → {doc.get('straatnaam')} {doc.get('huisnummer')}, {doc.get('postcode')} {doc.get('woonplaatsnaam')} (status={doc.get('status')}, type={doc.get('type')}, surface={doc.get('oppervlakte')})")
        return jsonify({
            'found': True,
            'query': query,
            'bag_data': {
                'street': doc.get('straatnaam'),
                'number': doc.get('huisnummer'),
                'number_letter': doc.get('huisletter'),
                'number_addition': doc.get('huisnummertoevoeging'),
                'zipcode': doc.get('postcode'),
                'town': doc.get('woonplaatsnaam'),
                'municipality': doc.get('gemeentenaam'),
                'province': doc.get('provincienaam'),
                'coordinates': doc.get('centroide_ll'),
                'purpose': doc.get('gebruiksdoel'),
                'surface': doc.get('oppervlakte'),
                'building_year': doc.get('bouwjaar'),
                'bag_id': doc.get('bag_id'),
                'status': doc.get('status'),
                'type': doc.get('type')
            }
        }), 200

    except httpx.RequestError as e:
        logger.error(f"Kadaster/PDOK lookup error: {e}")
        return jsonify({'error': f'Failed to lookup address: {str(e)}'}), 502
    except Exception as e:
        logger.error(f"Kadaster lookup unexpected error: {e}")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


@cms_bp.route('/api/politiebureau-lookup', methods=['POST'])
@login_required
def politiebureau_lookup():
    """
    Look up nearest police station (politiebureau) for an address.
    
    Accepts {address_id} or {lat, lon}. Looks up coordinates from
    kadaster_data or PDOK BAG, then calls api.politie.nl/politiebureaus/v1.
    """
    data = request.get_json() if request.is_json else request.form

    lat = lon = None
    address_info = {}

    address_id = data.get('address_id')
    if address_id:
        addr = Address.query.get(address_id)
        if not addr:
            return jsonify({'error': 'Address not found'}), 404
        addr.decrypt_fields()
        address_info = {
            'street': addr.street, 'number': addr.number,
            'zipcode': addr.zipcode, 'town': addr.town,
            'country': addr.country
        }

        # Check kadaster_data for stored coordinates
        if addr.kadaster_data:
            coords_str = addr.kadaster_data.get('coordinates')
            if coords_str and 'POINT(' in coords_str:
                c = coords_str.replace(
                    'POINT(', '').replace(')', '').strip().split(' ')
                if len(c) == 2:
                    lon, lat = float(c[0]), float(c[1])

            # Fallback: direct lat/lon keys
            if not lat and addr.kadaster_data.get('lat') and addr.kadaster_data.get('lon'):
                lat = float(addr.kadaster_data['lat'])
                lon = float(addr.kadaster_data['lon'])

        # Fallback: PDOK BAG lookup
        if not lat or not lon:
            query = ' '.join(filter(None, [
                addr.street, addr.number, addr.zipcode, addr.town
            ]))
            if query:
                try:
                    import httpx
                    pdok_url = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/free'
                    r = httpx.get(pdok_url, params={
                                  'q': query, 'rows': 1, 'fl': '*'}, timeout=10)
                    r.raise_for_status()
                    docs = r.json().get('response', {}).get('docs', [])
                    if docs:
                        cs = docs[0].get('centroide_ll')
                        if cs and 'POINT(' in cs:
                            c = cs.replace('POINT(', '').replace(
                                ')', '').strip().split(' ')
                            if len(c) == 2:
                                lon, lat = float(c[0]), float(c[1])
                except Exception:
                    pass
    # Direct coordinates
    if not lat or not lon:
        lat = data.get('lat')
        lon = data.get('lon')

    # Free-text query → PDOK BAG lookup
    if not lat or not lon:
        query = data.get('query') or ''
        if query:
            try:
                import httpx
                pdok_url = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/free'
                r = httpx.get(pdok_url, params={
                              'q': query, 'rows': 1, 'fl': '*'}, timeout=10)
                r.raise_for_status()
                docs = r.json().get('response', {}).get('docs', [])
                if docs and docs[0].get('centroide_ll'):
                    cs = docs[0]['centroide_ll']
                    if 'POINT(' in cs:
                        c = cs.replace('POINT(', '').replace(
                            ')', '').strip().split(' ')
                        if len(c) == 2:
                            lon, lat = float(c[0]), float(c[1])
            except Exception:
                pass

    if not lat or not lon:
        return jsonify({'error': 'Could not determine coordinates for this address'}), 400

    try:
        import httpx
        r = httpx.get('https://api.politie.nl/politiebureaus/v1',
                      params={'lat': lat, 'lon': lon}, timeout=10)
        r.raise_for_status()
        result = r.json()

        stations = result.get('politiebureaus', [])
        if not stations:
            return jsonify({'found': False,
                            'message': 'Geen politiebureaus gevonden in de buurt'}), 200

        s = stations[0]
        addr_bezoek = s.get('bezoekadres', {})
        station_addr = None
        if addr_bezoek.get('adres'):
            station_addr = (f"{addr_bezoek['adres']}, "
                            f"{addr_bezoek.get('postcode', '')} "
                            f"{addr_bezoek.get('plaats', '')}")

        return jsonify({
            'found': True,
            'station': {
                'name': s.get('naam'),
                'address': station_addr,
                'phone': s.get('telefoonnummer'),
                'opening_hours': s.get('openingstijden'),
                'url': s.get('url'),
                'location': s.get('locaties', [{}])[0] if s.get('locaties') else None
            },
            'address': address_info,
            'coordinates': {'lat': lat, 'lon': lon}
        }), 200

    except httpx.RequestError as e:
        return jsonify({'error': f'Failed to lookup police station: {str(e)}'}), 502
    except Exception as e:
        logger.error(f"Politiebureau lookup error: {e}")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/api/check-update', methods=['GET'])
@login_required
def check_update():
    """
    Check if a newer version or new commits are available on GitHub.
    Compares version + commit SHA to detect updates even without version bumps.
    Results are cached in-memory for 1 hour.
    """
    from version import get_version
    current_ver = get_version()

    repo = Setting.get('update_check_repo')
    if not repo:
        return jsonify({
            'update_available': False,
            'current_version': current_ver,
            'latest_version': current_ver,
            'check_enabled': False,
            'message': 'Update checking is disabled. Set update_check_repo in Settings.'
        })

    # In-memory cache on the app
    cache_key = '_update_check_cache'
    cache = current_app.config.get(cache_key, {})
    now = time.time()

    if cache.get('cached_at') and (now - cache['cached_at']) < 3600:
        return jsonify(cache['data'])

    try:
        import httpx

        # Fetch remote VERSION file
        ver_url = f'https://raw.githubusercontent.com/{repo}/master/VERSION'
        r = httpx.get(ver_url, timeout=10)
        r.raise_for_status()
        latest_ver = r.text.strip()

        # Fetch remote HEAD commit SHA via GitHub API
        local_sha = Setting.get('last_update_commit', '')
        remote_sha = local_sha
        try:
            api_url = f'https://api.github.com/repos/{repo}/commits/master'
            api_r = httpx.get(api_url, timeout=10, headers={
                              'Accept': 'application/vnd.github.v3.sha'})
            if api_r.status_code == 200:
                remote_sha = api_r.text.strip()
        except Exception:
            pass

        # If no stored local SHA, try to get it from the git repo and store it now
        if not local_sha and remote_sha:
            import subprocess as sp
            import shutil
            try:
                git_path = shutil.which('git') or '/usr/bin/git'
                r = sp.run(f'{git_path} rev-parse HEAD', shell=True,
                           capture_output=True, text=True, cwd=current_app.root_path, timeout=10)
                if r.returncode == 0:
                    local_sha = r.stdout.strip()
                    Setting.set('last_update_commit', local_sha,
                                'Last pulled commit SHA (auto-updated)', 'general')
                    logger.info(f"Stored initial commit SHA: {local_sha[:12]}")
            except Exception:
                pass

        current_parts = [int(x) for x in current_ver.split('.')]
        latest_parts = [int(x) for x in latest_ver.split('.')]
        version_update = latest_parts > current_parts
        commits_update = bool(
            remote_sha and local_sha and remote_sha != local_sha and not version_update)
        update_available = version_update or commits_update

        data = {
            'update_available': update_available,
            'version_update': version_update,
            'commits_update': commits_update,
            'current_version': current_ver,
            'latest_version': latest_ver if version_update else current_ver,
            'check_enabled': True,
            'repo': repo,
            'remote_sha': remote_sha,
            'local_sha': local_sha,
        }

        current_app.config[cache_key] = {'data': data, 'cached_at': now}
        return jsonify(data)

    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        return jsonify({
            'update_available': False,
            'current_version': current_ver,
            'latest_version': None,
            'check_enabled': True,
            'error': str(e)
        })


@cms_bp.route('/admin/do-update', methods=['POST'])
@login_required
@admin_required
def do_update():
    """
    Run update: backup, git pull, pip upgrade, restart services.
    Admin only. Runs synchronously and streams status via JSON responses.
    """
    import subprocess
    import sys
    from version import get_version

    current_ver = get_version()
    results = []

    def step(msg, cmd, cwd=None):
        results.append({'step': msg, 'status': 'running'})
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               cwd=cwd or current_app.root_path, timeout=120)
            if r.returncode == 0:
                results[-1] = {'step': msg, 'status': 'ok',
                               'output': r.stdout.strip()}
            elif r.returncode < 0 and 'restart' in msg.lower():
                results[-1] = {'step': msg, 'status': 'ok',
                               'output': 'Service restarted (process killed by signal, expected)'}
            else:
                output = r.stderr.strip() or r.stdout.strip(
                ) or f'Command failed (exit code {r.returncode})'
                results[-1] = {'step': msg,
                               'status': 'error', 'output': output}
                logger.error(f"Update step failed: {msg}\n{output}")
        except Exception as e:
            results[-1] = {'step': msg, 'status': 'error', 'output': str(e)}
            logger.error(f"Update step exception: {msg}\n{e}")

    import shutil
    project_root = current_app.root_path

    # Step 1: Database backup (SQLite only)
    db_path = current_app.config.get(
        'SQLALCHEMY_DATABASE_URI', 'sqlite:///cms.db')
    if db_path.startswith('sqlite'):
        db_file = db_path.replace('sqlite:///', '')
        step('Backup database',
             f'cp "{db_file}" "{db_file}.backup.$(date +%Y%m%d_%H%M%S)"')

    # Step 2: Git pull (use full path, systemd PATH may not include /usr/bin)
    git_path = shutil.which('git') or '/usr/bin/git'
    step('Pull latest code',
         f'{git_path} pull origin master', cwd=project_root)

    # Step 3: Install dependencies
    step('Update Python packages', f'{sys.executable} -m pip install -r requirements.txt --upgrade',
         cwd=project_root)

    # Step 4: Run db.create_all() for any new tables
    step('Apply database migrations',
         f'{sys.executable} -c "from app import app; from cms.models import db; import flask; app.app_context().push(); db.create_all(); print(\'Migrations OK\')"',
         cwd=project_root)

    # Step 5: Restart (uses sudo via sudoers rule set by install.sh)
    step('Restart services', '/usr/bin/sudo /usr/bin/systemctl restart osint-dashboard',
         cwd=project_root)

    success = all(r['status'] == 'ok' for r in results)

    # Store local HEAD SHA after successful update for commit-based change detection
    if success:
        try:
            import subprocess as sp
            git_path = shutil.which('git') or '/usr/bin/git'
            sha_result = sp.run(f'{git_path} rev-parse HEAD', shell=True,
                                capture_output=True, text=True, cwd=project_root, timeout=15)
            if sha_result.returncode == 0:
                head_sha = sha_result.stdout.strip()
                Setting.set('last_update_commit', head_sha,
                            'Last pulled commit SHA (auto-updated)', 'general')
                logger.info(f"Stored last update commit: {head_sha[:12]}")
        except Exception as e:
            logger.warning(f"Failed to store commit SHA: {e}")

    return jsonify({
        'success': success,
        'current_version': current_ver,
        'results': results,
        'message': 'Update completed successfully' if success else 'Update had errors, check results'
    }), 200 if success else 500


@cms_bp.route('/api/phone-lookup', methods=['POST'])
@login_required
def phone_lookup():
    """
    Look up a phone number: validation, carrier, location, line type, WhatsApp/Telegram.
    Uses phonenumbers library + free Bedrijfsdata API for NL numbers.
    """
    data = request.get_json() if request.is_json else request.form
    phone = (data.get('phone') or '').strip()

    if not phone:
        return jsonify({'error': 'Phone number required'}), 400

    result = {
        'phone': phone,
        'valid': False,
        'formatted': None,
        'country': None,
        'country_code': None,
        'region': None,
        'carrier': None,
        'line_type': None,
        'timezone': None,
        'normalized': None,
        'services': {},
        'nl_info': None
    }

    try:
        import httpx
        import phonenumbers
        from phonenumbers import geocoder, carrier, timezone as pn_tz

        parsed = phonenumbers.parse(phone, 'NL')
        result['valid'] = phonenumbers.is_valid_number(parsed)
        result['formatted'] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164)
        result['country_code'] = f"+{parsed.country_code}"

        try:
            result['country'] = geocoder.description_for_number(parsed, 'en')
        except Exception:
            pass

        try:
            result['region'] = geocoder.description_for_number(parsed, 'nl')
        except Exception:
            pass

        try:
            result['carrier'] = carrier.name_for_number(parsed, 'nl')
        except Exception:
            pass

        try:
            ntype = phonenumbers.number_type(parsed)
            line_map = {
                phonenumbers.PhoneNumberType.MOBILE: 'Mobile',
                phonenumbers.PhoneNumberType.FIXED_LINE: 'Fixed Line',
                phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: 'Fixed Line or Mobile',
                phonenumbers.PhoneNumberType.PAGER: 'Pager',
                phonenumbers.PhoneNumberType.PERSONAL_NUMBER: 'Personal Number',
                phonenumbers.PhoneNumberType.PREMIUM_RATE: 'Premium Rate',
                phonenumbers.PhoneNumberType.SHARED_COST: 'Shared Cost',
                phonenumbers.PhoneNumberType.TOLL_FREE: 'Toll Free',
                phonenumbers.PhoneNumberType.UAN: 'UAN',
                phonenumbers.PhoneNumberType.VOIP: 'VoIP',
            }
            result['line_type'] = line_map.get(ntype, str(ntype))
        except Exception:
            pass

        try:
            tz = pn_tz.time_zones_for_number(parsed)
            result['timezone'] = tz[0] if tz else None
        except Exception:
            pass

        normalized = re.sub(r'[^0-9]', '', result['formatted'])
        result['normalized'] = normalized

        # WhatsApp check
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            try:
                wa_url = f'https://api.whatsapp.com/send?phone={normalized}'
                wa_resp = client.get(
                    wa_url, headers={'User-Agent': 'Mozilla/5.0'})
                wa_text = wa_resp.text.lower()
                if 'phone number is not on whatsapp' in wa_text:
                    result['services']['whatsapp'] = {'exists': False}
                else:
                    result['services']['whatsapp'] = {
                        'exists': True, 'url': f'https://wa.me/{normalized}'}
            except Exception:
                result['services']['whatsapp'] = {
                    'exists': None, 'note': 'Check failed'}

            # Telegram check
            try:
                tg_url = f'https://t.me/+{normalized}'
                tg_resp = client.get(
                    tg_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                tg_text = tg_resp.text.lower()
                if tg_resp.status_code == 400 or 'join' in tg_text or 'subscribe' in tg_text:
                    result['services']['telegram'] = {
                        'exists': True, 'url': tg_url}
                elif tg_resp.status_code == 200:
                    result['services']['telegram'] = {'exists': False}
                else:
                    result['services']['telegram'] = {
                        'exists': None, 'note': 'Unable to verify'}
            except Exception:
                result['services']['telegram'] = {
                    'exists': None, 'note': 'Check failed'}

        # Free NL-specific lookup via Bedrijfsdata API
        if result['country_code'] == '+31':
            try:
                bd_url = 'https://free.bedrijfsdata.nl/v1.1/phone'
                bd_params = {'country_code': 'nl',
                             'phone': phone.lstrip('+').lstrip('00')}
                bd_resp = httpx.get(bd_url, params=bd_params, timeout=10)
                if bd_resp.status_code == 200:
                    bd_data = bd_resp.json().get('phone', {})
                    result['nl_info'] = {
                        'valid': bd_data.get('valid') == 1,
                        'region': bd_data.get('region'),
                        'carrier': bd_data.get('carrier'),
                        'is_mobile': bd_data.get('ismobile') == 1
                    }
                    if bd_data.get('region') and not result.get('region'):
                        result['region'] = bd_data['region']
                    if bd_data.get('carrier') and not result.get('carrier'):
                        result['carrier'] = bd_data['carrier']
            except Exception:
                pass

        logger.info(
            f"Phone lookup: {phone} → valid={result['valid']}, carrier={result['carrier']}, region={result['region']}, wa={result['services'].get('whatsapp', {}).get('exists')}")
        return jsonify(result), 200

    except ImportError:
        return jsonify({'error': 'phonenumbers library not installed'}), 500
    except Exception as e:
        logger.error(f"Phone lookup error: {e}")
        return jsonify({'error': f'Phone lookup failed: {str(e)}'}), 500


@cms_bp.route('/api/email-check', methods=['POST'])
@login_required
def email_check():
    """
    Validate an email address and check for known breaches.

    Checks:
    - Email format validity (regex)
    - MX record resolution (domain can receive mail)
    - Disposable domain detection
    - Have I Been Pwned breaches (if HIBP_API_KEY is set)
    - EmailRep reputation (free tier, public API)
    """
    data = request.get_json() if request.is_json else request.form
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'error': 'Email address required'}), 400

    import socket
    import httpx

    result = {
        'email': email,
        'valid_format': False,
        'domain': None,
        'has_mx': False,
        'disposable': False,
        'hibp_found': False,
        'hibp_breaches': [],
        'emailrep': None,
        'search_links': [],
        'error': None
    }

    # --- Format validation ---
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        result['error'] = 'Invalid email format'
        return jsonify(result), 200

    result['valid_format'] = True
    domain = email.split('@')[1]
    result['domain'] = domain

    # --- Disposable domain check ---
    disposable_domains = {
        'mailinator.com', 'guerrillamail.com', 'tempmail.com', 'throwaway.email',
        'yopmail.com', 'sharklasers.com', 'trashmail.com', '10minutemail.com',
        'mailnator.com', 'temp-mail.org', 'getairmail.com', 'tempinbox.com',
        'spamgourmet.com', 'mailexpire.com', 'maildrop.cc', 'burnermail.io',
        'inboxbear.com', 'discard.email', 'mintemail.com', 'mailforspam.com',
    }
    if domain.lower() in disposable_domains:
        result['disposable'] = True

    # --- MX record check ---
    try:
        socket.getaddrinfo(domain, 25)
        result['has_mx'] = True
    except socket.gaierror:
        result['has_mx'] = False

    # --- Have I Been Pwned ---
    hibp_key = os.environ.get('HIBP_API_KEY', '')
    if hibp_key:
        try:
            resp = httpx.get(
                f'https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=false',
                headers={
                    'hibp-api-key': hibp_key,
                    'User-Agent': 'Iveras-OSINT-Dashboard/3.0'
                },
                timeout=15
            )
            if resp.status_code == 200:
                breaches = resp.json()
                result['hibp_found'] = True
                result['hibp_breaches'] = [{
                    'name': b.get('Name'),
                    'domain': b.get('Domain'),
                    'date': b.get('BreachDate'),
                    'data_classes': b.get('DataClasses', []),
                    'description': b.get('Description', '')[:200],
                } for b in breaches]
            elif resp.status_code == 404:
                result['hibp_found'] = False
            elif resp.status_code == 401:
                result['hibp_found'] = False
                logger.warning("HIBP API key rejected")
        except Exception as e:
            logger.warning(f"HIBP lookup failed: {e}")

    # --- EmailRep ---
    try:
        eresp = httpx.get(
            f'https://emailrep.io/{email}',
            headers={'User-Agent': 'Iveras-OSINT-Dashboard/3.0'},
            timeout=10
        )
        if eresp.status_code == 200:
            result['emailrep'] = eresp.json()
    except Exception as e:
        logger.warning(f"EmailRep lookup failed: {e}")

    # --- Search links ---
    result['search_links'] = [
        {'label': 'Have I Been Pwned',
            'url': f'https://haveibeenpwned.com/account/{email}'},
        {'label': 'EmailRep', 'url': f'https://emailrep.io/{email}'},
        {'label': 'Hunter.io', 'url': f'https://hunter.io/search/{domain}'},
        {'label': 'Dehashed', 'url': f'https://dehashed.com/search?query={email}'},
        {'label': 'Google', 'url': f'https://www.google.com/search?q={email}'},
    ]

    logger.info(
        f"Email check: {email} → valid={result['valid_format']}, mx={result['has_mx']}, hibp={result['hibp_found']}")
    return jsonify(result), 200


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
    Case.query.get_or_404(case_id)
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
                page = browser.new_page(
                    viewport={'width': 1280, 'height': 720})
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
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return '', 404

    filepath = get_screenshot_path(case_id, screenshot.filename)

    if not os.path.exists(filepath):
        return '', 404

    try:
        # First try to generate a proper thumbnail
        try:
            with Image.open(filepath) as img:
                img.thumbnail((200, 200), Image.Resampling.LANCZOS)
                thumb_io = io.BytesIO()
                img.save(thumb_io, format='PNG')
            thumb_io.seek(0)
            return send_file(
                thumb_io,
                mimetype='image/png',
                as_attachment=False
            )
        except Exception as e:
            logger.warning(
                f"Thumbnail generation failed, serving original: {e}")
            # Fallback: serve original image
            return send_file(filepath, mimetype='image/png', as_attachment=False)
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return '', 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>/view')
@login_required
@case_access_required
def view_screenshot(case_id: str, screenshot_id: str):
    """View the full screenshot."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return '', 404

    filepath = get_screenshot_path(case_id, screenshot.filename)

    if not os.path.exists(filepath):
        return '', 404

    try:
        # Detect mimetype from file extension
        ext = screenshot.filename.rsplit(
            '.', 1)[-1].lower() if '.' in screenshot.filename else 'png'
        mimetype_map = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        mimetype = mimetype_map.get(ext, 'image/png')

        return send_file(
            filepath,
            mimetype=mimetype,
            as_attachment=False,
            download_name=screenshot.title or screenshot.filename
        )
    except Exception as e:
        logger.error(f"View screenshot error: {e}")
        return '', 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>')
@login_required
@case_access_required
def get_screenshot(case_id: str, screenshot_id: str):
    """Get screenshot details."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return jsonify({'error': 'Screenshot not found'}), 404

    return jsonify(screenshot.to_dict())


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>', methods=['DELETE'])
@login_required
@case_access_required
@case_edit_required
def delete_screenshot(case_id: str, screenshot_id: str):
    """Delete a screenshot."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return jsonify({'error': 'Screenshot not found'}), 404

    try:
        # Delete the file
        filepath = get_screenshot_path(case_id, screenshot.filename)
        if os.path.exists(filepath):
            os.remove(filepath)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='delete',
            entity_type='screenshot',
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Deleted screenshot: {screenshot.title or screenshot.filename}"
        )

        # Delete database record
        db.session.delete(screenshot)
        db.session.commit()

        return jsonify({'message': 'Screenshot deleted'}), 200

    except Exception as e:
        logger.error(f"Screenshot delete error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Document Upload Routes
# =============================================================================

from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg',
                      'doc', 'docx', 'xls', 'xlsx', 'txt', 'csv'}
UPLOAD_FOLDER = 'uploads'


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@cms_bp.route('/cases/<case_id>/upload', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def upload_case_document(case_id: str):
    """Upload a document to a case."""
    Case.query.get_or_404(case_id)

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400

    # Create upload directory if not exists
    upload_dir = os.path.join(current_app.root_path,
                              'static', UPLOAD_FOLDER, 'cases', case_id)
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
    upload_dir = os.path.join(current_app.root_path,
                              'static', UPLOAD_FOLDER, 'subjects', subject_id)
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

    if not document.storage_path:
        return jsonify({'error': 'Document file not found on server'}), 404

    file_path = os.path.join(current_app.root_path,
                             'static', document.storage_path)

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
    file_path = os.path.join(current_app.root_path,
                             'static', document.storage_path)
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
    Case.query.get_or_404(case_id)
    records = FinancialRecord.query.filter_by(
        case_id=case_id, is_deleted=False).all()

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
                reminder_date = datetime.strptime(
                    reminder_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                try:
                    reminder_date = datetime.strptime(
                        reminder_date_str, '%Y-%m-%d %H:%M')
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
            notify_dashboard=data.get('notify_dashboard') in [
                                      'on', 'true', '1', True]
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
        fields = ['title', 'description', 'priority',
                  'reminder_type', 'recurrence', 'assigned_to']
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
                reminder.reminder_date = datetime.strptime(
                    reminder_date_str, '%Y-%m-%dT%H:%M')
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
        reminder.notify_email = data.get('notify_email') in [
                                         'on', 'true', '1', True]
        reminder.notify_dashboard = data.get('notify_dashboard') in [
                                             'on', 'true', '1', True]

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


# =============================================================================
# SpiderFoot OSINT Integration Routes
# =============================================================================

def get_spiderfoot_config():
    """Get SpiderFoot configuration from settings."""
    from .spiderfoot_service import SpiderFootConfig

    base_url = Setting.get(
        'spiderfoot_url', 'http://localhost:5001') or 'http://localhost:5001'
    username = Setting.get('spiderfoot_username', 'admin') or 'admin'
    password = Setting.get('spiderfoot_password', '') or ''

    return SpiderFootConfig(
        base_url=base_url,
        username=username,
        password=password
    )


def get_spiderfoot_service():
    """Get SpiderFoot service instance."""
    if not SPIDERFOOT_AVAILABLE:
        return None
    return SpiderFootService(get_spiderfoot_config())


@cms_bp.route('/spiderfoot')
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_index():
    """SpiderFoot integration dashboard."""
    try:
        sf_service = get_spiderfoot_service()
        available = sf_service.is_available() if sf_service else False
        server_info = sf_service.get_server_info() if sf_service and available else None
    except Exception:
        sf_service = None
        available = False
        server_info = None

    db.session.rollback()  # Clear any stale state from previous requests

    db_scans = SpiderFootScan.query.filter_by(
        is_deleted=False
    ).order_by(SpiderFootScan.created_at.desc()).limit(10).all()

    # Also fetch scans directly from SpiderFoot API and merge
    sf_scans = []
    if available:
        try:
            sf_scans = sf_service.get_scan_list() or []
        except Exception:
            pass

    # Merge: DB scan IDs we already have
    db_sf_ids = {s.scan_id for s in db_scans}

    # Add SF API scans not yet in DB (as dicts for template)
    recent_scans = list(db_scans)
    for sf_scan in sf_scans:
        if isinstance(sf_scan, list) and len(sf_scan) >= 7:
            sf_id = sf_scan[0]
            if sf_id and sf_id not in db_sf_ids:
                status_raw = sf_scan[6]
                api_status = status_raw.lower() if status_raw else 'unknown'
                mapped_status = {'finished': 'completed', 'error': 'failed',
                                 'aborted': 'cancelled'}.get(api_status, api_status)
                recent_scans.append({
                    'id': sf_id,
                    'scan_id': sf_id,
                    'scan_name': sf_scan[1] if len(sf_scan) > 1 else 'SpiderFoot Scan',
                    'target_value': sf_scan[2] if len(sf_scan) > 2 else '',
                    'target_type': '',
                    'status': mapped_status,
                    'progress': 100 if mapped_status == 'completed' else 0,
                    'result_count': sf_scan[7] if len(sf_scan) > 7 else 0,
                    'profile': '',
                    'use_case': '',
                    'created_at': sf_scan[3] if len(sf_scan) > 3 else None,
                    'from_spiderfoot': True,
                })
        elif isinstance(sf_scan, dict):
            sf_id = sf_scan.get('scan_id') or sf_scan.get('id')
            if sf_id and sf_id not in db_sf_ids:
                recent_scans.append({
                    'id': sf_id,
                    'scan_id': sf_id,
                    'scan_name': sf_scan.get('scan_name', sf_scan.get('title', 'SpiderFoot Scan')),
                    'target_value': sf_scan.get('target', sf_scan.get('target_value', '')),
                    'target_type': sf_scan.get('target_type', ''),
                    'status': (sf_scan.get('status') or '').lower(),
                    'progress': sf_scan.get('progress', 0),
                    'result_count': sf_scan.get('resultCount', sf_scan.get('result_count', 0)),
                    'profile': sf_scan.get('profile', ''),
                    'use_case': sf_scan.get('use_case', ''),
                    'created_at': None,
                    'from_spiderfoot': True,
                })
    # Normalize created_at for sorting (convert datetimes to strings for dicts only)
    for s in recent_scans:
        if isinstance(s, dict):
            if isinstance(s.get('created_at'), datetime):
                s['created_at'] = s['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    recent_scans.sort(key=lambda s: s.get('created_at', '') if isinstance(s, dict) else (
        s.created_at.strftime('%Y-%m-%d %H:%M:%S') if s.created_at else ''), reverse=True)
    recent_scans = recent_scans[:10]

    # Get status counts including SF scans
    status_counts = {'running': 0, 'completed': 0, 'pending': 0, 'failed': 0}
    db_counts = {
        'running': SpiderFootScan.query.filter_by(status='running', is_deleted=False).count(),
        'completed': SpiderFootScan.query.filter_by(status='completed', is_deleted=False).count(),
        'pending': SpiderFootScan.query.filter_by(status='pending', is_deleted=False).count(),
        'failed': SpiderFootScan.query.filter_by(status='failed', is_deleted=False).count(),
    }
    status_map = {'finished': 'completed', 'running': 'running', 'pending': 'pending',
                  'failed': 'failed', 'error': 'failed', 'aborted': 'cancelled', 'cancelled': 'cancelled'}
    for s in sf_scans:
        if isinstance(s, list) and len(s) >= 7:
            raw_st = (s[6] or '').lower()
        elif isinstance(s, dict):
            raw_st = (s.get('status') or '').lower()
        else:
            raw_st = ''
        mapped_st = status_map.get(raw_st, raw_st)
        if mapped_st in status_counts:
            status_counts[mapped_st] += 1
    for k in status_counts:
        status_counts[k] += db_counts.get(k, 0)

    profiles = SpiderFootService.INVESTIGATION_PROFILES if SpiderFootService else {}
    use_cases = SpiderFootService.USE_CASES if SpiderFootService else {}
    target_types = SpiderFootService.TARGET_TYPES if SpiderFootService else {}

    return render_template('cms/spiderfoot/index.html',
                           available=available,
                           server_info=server_info,
                           recent_scans=recent_scans,
                           status_counts=status_counts,
                           profiles=profiles,
                           use_cases=use_cases,
                           target_types=target_types
                           )


@cms_bp.route('/spiderfoot/scan', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_scan():
    """Start a new SpiderFoot scan."""
    if request.method == 'GET':
        # Show scan form
        cases = Case.query.filter_by(is_deleted=False).order_by(
            Case.case_number.desc()).all()
        subjects = Subject.query.filter_by(
            is_deleted=False).order_by(Subject.name).all()

        profiles = SpiderFootService.INVESTIGATION_PROFILES if SpiderFootService else {}
        use_cases = SpiderFootService.USE_CASES if SpiderFootService else {}
        target_types = SpiderFootService.TARGET_TYPES if SpiderFootService else {}

        # Get recent unique targets for quick-select
        recent_scans = SpiderFootScan.query.filter_by(is_deleted=False)\
            .order_by(SpiderFootScan.created_at.desc()).limit(20).all()
        seen = set()
        recent_targets = []
        for s in recent_scans:
            key = (s.target_value or '', s.target_type or '')
            if key not in seen:
                seen.add(key)
                recent_targets.append(
                    {'target': s.target_value, 'type': s.target_type})

        return render_template('cms/spiderfoot/scan.html',
                               cases=cases,
                               subjects=subjects,
                               profiles=profiles,
                               use_cases=use_cases,
                               target_types=target_types,
                               recent_targets=recent_targets
                               )

    # POST - Start scan
    data = request.get_json() if request.is_json else request.form

    target = data.get('target')
    target_type = data.get('target_type', 'DOMAIN_NAME')
    scan_name = data.get('scan_name')
    case_id = data.get('case_id')
    subject_id = data.get('subject_id')
    profile = data.get('profile')
    use_case = data.get('use_case', 'passive')

    if not target:
        if request.is_json:
            return jsonify({'error': 'Target is required'}), 400
        flash('Target is required.', 'error')
        return redirect(url_for('cms.spiderfoot_scan'))

    sf_service = get_spiderfoot_service()

    if not sf_service or not sf_service.is_available():
        if request.is_json:
            return jsonify({'error': 'SpiderFoot server is not available'}), 503
        flash('SpiderFoot server is not available. Please check the settings.', 'error')
        return redirect(url_for('cms.spiderfoot_index'))

    # Start the scan
    result = sf_service.start_scan(
        target=target,
        target_type=target_type,
        scan_name=scan_name,
        use_case=use_case,
        profile=profile
    )

    if not result or not result.get('scan_id'):
        if request.is_json:
            return jsonify({'error': 'Failed to start scan'}), 500
        flash('Failed to start SpiderFoot scan.', 'error')
        return redirect(url_for('cms.spiderfoot_index'))

    # Create local scan record
    scan_record = SpiderFootScan(
        scan_id=result['scan_id'],
        scan_name=scan_name or f"Scan of {target}",
        target_value=target,
        target_type=target_type,
        case_id=case_id if case_id else None,
        subject_id=subject_id if subject_id else None,
        use_case=use_case,
        profile=profile,
        module_ids=SpiderFootService.INVESTIGATION_PROFILES.get(profile, {}).get(
            'modules', []) if (SpiderFootService and profile) else [],
        status='pending',
        created_by=current_user.id
    )
    scan_record.update_status('running', 0)

    db.session.add(scan_record)

    AuditLog.log(
        user_id=current_user.id,
        action='spiderfoot_scan_start',
        entity_type='spiderfoot_scan',
        entity_id=scan_record.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Started SpiderFoot scan: {scan_record.scan_name} for {target}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({
            'message': 'Scan started',
            'scan': scan_record.to_dict()
        }), 201

    flash('SpiderFoot scan started.', 'success')
    return redirect(url_for('cms.spiderfoot_scan_status', scan_id=scan_record.id))


@cms_bp.route('/spiderfoot/scan/<scan_id>')
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_scan_status(scan_id: str):
    """View SpiderFoot scan status and results."""
    from datetime import datetime as dt

    # Try Iveras DB record first, fall back to direct SpiderFoot scan ID
    scan_record = SpiderFootScan.query.get(scan_id)

    sf_service = get_spiderfoot_service()
    if not sf_service:
        if scan_record:
            return render_template('cms/spiderfoot/view.html',
                                   scan=scan_record, sf_status=None, results=[], result_summary={}, available=False)
        abort(503)

    # Determine the actual SpiderFoot scan_id
    sf_scan_id = scan_record.scan_id if scan_record else scan_id

    # Refresh status from SpiderFoot
    sf_status = sf_service.get_scan_status(sf_scan_id)

    if sf_status:
        status = sf_status.get('status', 'unknown')
        status_lower = status.lower()
        progress = sf_status.get('progress', 0)

        # Create or update DB record
        if not scan_record:
            scan_record = SpiderFootScan(
                id=scan_id,
                scan_id=sf_scan_id,
                scan_name=sf_status.get('scan_name', sf_status.get(
                    'title', f'Scan {sf_scan_id[:8]}')),
                target_value=sf_status.get(
                    'target', sf_status.get('target_value', '')),
                target_type=sf_status.get('target_type', ''),
                status=status,
                progress=progress,
                created_by='system'
            )
            scan_record.created_at = dt.utcnow()
            db.session.add(scan_record)
        else:
            scan_record.status = status
            scan_record.progress = progress

        # Map status
        if status_lower in ['completed', 'finished']:
            scan_record.update_status('completed')
        elif status_lower == 'running':
            scan_record.update_status('running', progress)
        elif status_lower in ['failed', 'error']:
            scan_record.update_status('failed')
        elif status_lower in ['aborted', 'cancelled']:
            scan_record.update_status('cancelled')

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Get results if completed
    results = []
    result_summary = {}
    status_lower = (scan_record.status or '').lower()
    if status_lower in ['completed', 'finished']:
        results = sf_service.get_scan_results(sf_scan_id, limit=5000)
        result_summary = sf_service.get_result_summary(results)
        if scan_record:
            scan_record.result_count = len(results)
            scan_record.result_summary = result_summary
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    return render_template('cms/spiderfoot/view.html',
                           scan=scan_record,
                           sf_status=sf_status,
                           results=results[:100],  # Limit displayed results
                           result_summary=result_summary,
                           available=sf_service.is_available()
                           )


@cms_bp.route('/spiderfoot/scan/<scan_id>/refresh', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_refresh_scan(scan_id: str):
    """Refresh SpiderFoot scan status."""
    scan_record = SpiderFootScan.query.get_or_404(scan_id)

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({'error': 'SpiderFoot server not available'}), 503

    sf_status = sf_service.get_scan_status(scan_record.scan_id)

    if sf_status:
        status = sf_status.get('status', 'unknown')
        status_lower = status.lower()  # Normalize to lowercase
        progress = sf_status.get('progress', 0)
        scan_record.status = status
        scan_record.progress = progress

        if status_lower in ['completed', 'finished']:
            results = sf_service.get_scan_results(
                scan_record.scan_id, limit=5000)
            result_summary = sf_service.get_result_summary(results)
            scan_record.result_count = len(results)
            scan_record.result_summary = result_summary
            scan_record.update_status('completed')
        elif status_lower in ['failed', 'error']:
            scan_record.update_status('failed')
        elif status_lower in ['aborted', 'cancelled']:
            scan_record.update_status('cancelled')

        db.session.commit()

    return jsonify(scan_record.to_dict())


@cms_bp.route('/spiderfoot/scan/<scan_id>/stop', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_stop_scan(scan_id: str):
    """Stop a running SpiderFoot scan."""
    scan_record = SpiderFootScan.query.get_or_404(scan_id)

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({'error': 'SpiderFoot server not available'}), 503

    if sf_service.stop_scan(scan_record.scan_id):
        scan_record.update_status('cancelled')
        db.session.commit()

        AuditLog.log(
            user_id=current_user.id,
            action='spiderfoot_scan_stop',
            entity_type='spiderfoot_scan',
            entity_id=scan_record.id,
            ip_address=request.remote_addr,
            description=f"Stopped SpiderFoot scan: {scan_record.scan_name}"
        )
        db.session.commit()

        return jsonify({'message': 'Scan stopped', 'scan': scan_record.to_dict()})

    return jsonify({'error': 'Failed to stop scan'}), 500


@cms_bp.route('/spiderfoot/scan/<scan_id>/delete', methods=['POST'])
@login_required
@admin_required
def spiderfoot_delete_scan(scan_id: str):
    """Delete a SpiderFoot scan record."""
    scan_record = SpiderFootScan.query.get_or_404(scan_id)

    sf_service = get_spiderfoot_service()

    # Try to delete from SpiderFoot as well
    try:
        if scan_record.status not in ['running']:
            sf_service.delete_scan(scan_record.scan_id)
    except Exception as e:
        logger.warning(f"Could not delete SpiderFoot scan: {e}")

    scan_record.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action='spiderfoot_scan_delete',
        entity_type='spiderfoot_scan',
        entity_id=scan_record.id,
        ip_address=request.remote_addr,
        description=f"Deleted SpiderFoot scan record: {scan_record.scan_name}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({'message': 'Scan deleted'})

    flash('Scan record deleted.', 'info')
    return redirect(url_for('cms.spiderfoot_index'))


@cms_bp.route('/spiderfoot/scan/<scan_id>/results')
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_scan_results(scan_id: str):
    """Get full SpiderFoot scan results as JSON."""
    scan_record = SpiderFootScan.query.get_or_404(scan_id)

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({'error': 'SpiderFoot server not available'}), 503

    element_type = request.args.get('type')  # Filter by type
    limit = request.args.get('limit', 10000, type=int)

    results = sf_service.get_scan_results(
        scan_record.scan_id, element_type=element_type, limit=limit)
    summary = sf_service.get_result_summary(results)

    return jsonify({
        'scan': scan_record.to_dict(),
        'results': results,
        'summary': summary,
        'total': len(results)
    })


@cms_bp.route('/spiderfoot/scan/<scan_id>/import', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_import_results(scan_id: str):
    """Import SpiderFoot scan results as Iveras findings."""
    scan_record = SpiderFootScan.query.get_or_404(scan_id)

    if not scan_record.case_id:
        return jsonify({'error': 'Scan must be linked to a case to import findings'}), 400

    sf_service = get_spiderfoot_service()

    if not sf_service.is_available():
        return jsonify({'error': 'SpiderFoot server not available'}), 503

    data = request.get_json() if request.is_json else request.form

    # Filter options
    element_types = data.get('element_types', [])  # Only import these types
    min_length = data.get('min_length', 3)  # Minimum data length
    limit = data.get('limit', 1000)

    results = sf_service.get_scan_results(scan_record.scan_id, limit=limit)

    imported_count = 0
    skipped_count = 0

    for result in results:
        sf_type = result.get('type', '')

        # Filter by type if specified
        if element_types and sf_type not in element_types:
            skipped_count += 1
            continue

        # Filter by data length
        data_val = result.get('data', '') or result.get('dataTransformed', '')
        if len(data_val) < min_length:
            skipped_count += 1
            continue

        # Map to Iveras finding
        finding_data = sf_service.map_to_iveras_finding(
            result,
            case_id=scan_record.case_id,
            subject_id=scan_record.subject_id
        )

        finding = Finding(
            case_id=finding_data['case_id'],
            subject_id=finding_data.get('subject_id'),
            title=finding_data['title'][:300],
            content=finding_data['content'],
            source_url=finding_data.get('source_url'),
            source_type='spiderfoot',
            reliability_score=finding_data.get('reliability_score', 7),
            confidence_level=finding_data.get('confidence_level', 'medium'),
            finding_type=finding_data.get('finding_type', 'general'),
            tags=finding_data.get('tags', ['spiderfoot']),
            created_by=current_user.id
        )

        db.session.add(finding)
        imported_count += 1

    AuditLog.log(
        user_id=current_user.id,
        action='spiderfoot_import',
        entity_type='spiderfoot_scan',
        entity_id=scan_record.id,
        ip_address=request.remote_addr,
        case_id=scan_record.case_id,
        description=f"Imported {imported_count} findings from SpiderFoot scan"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({
            'message': f'Imported {imported_count} findings',
            'imported': imported_count,
            'skipped': skipped_count
        })

    flash(f'Imported {imported_count} findings.', 'success')
    return redirect(url_for('cms.view_case', case_id=scan_record.case_id))


@cms_bp.route('/spiderfoot/scans')
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_scans():
    """List all SpiderFoot scans."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    status = request.args.get('status', '')
    search = request.args.get('search', '')

    query = SpiderFootScan.query.filter_by(is_deleted=False)

    if status:
        query = query.filter_by(status=status)

    if search:
        query = query.filter(
            db.or_(
                SpiderFootScan.scan_name.ilike(f'%{search}%'),
                SpiderFootScan.target_value.ilike(f'%{search}%')
            )
        )

    pagination = query.order_by(SpiderFootScan.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('cms/spiderfoot/list.html',
                           scans=pagination.items,
                           pagination=pagination,
                           filters={'status': status, 'search': search}
                           )


@cms_bp.route('/spiderfoot/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def spiderfoot_settings():
    """Manage SpiderFoot settings."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form

        # Update settings
        Setting.set('spiderfoot_url', data.get('url', 'http://localhost:5001'),
                    description='SpiderFoot server URL', category='spiderfoot')
        Setting.set('spiderfoot_username', data.get('username', 'admin'),
                    description='SpiderFoot login username', category='spiderfoot')
        Setting.set('spiderfoot_password', data.get('password', ''),
                    description='SpiderFoot login password', category='spiderfoot', encrypt=True)

        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='settings',
            entity_id='spiderfoot',
            ip_address=request.remote_addr,
            description="Updated SpiderFoot settings"
        )
        db.session.commit()

        if request.is_json:
            return jsonify({'message': 'Settings saved'})

        flash('SpiderFoot settings saved.', 'success')
        return redirect(url_for('cms.spiderfoot_settings'))

    # GET - Show settings form
    settings = {
        'url': Setting.get('spiderfoot_url', 'http://localhost:5001'),
        'username': Setting.get('spiderfoot_username', 'admin'),
        'password': Setting.get('spiderfoot_password', ''),
    }

    # Test connection
    sf_service = get_spiderfoot_service()
    connection_ok = sf_service.is_available()
    server_info = sf_service.get_server_info() if connection_ok else None

    return render_template('cms/spiderfoot/settings.html',
                           settings=settings,
                           connection_ok=connection_ok,
                           server_info=server_info
                           )


@cms_bp.route('/spiderfoot/settings/test', methods=['POST'])
@login_required
@admin_required
def spiderfoot_test_connection():
    """Test SpiderFoot connection."""
    data = request.get_json() if request.is_json else request.form

    url = data.get('url', 'http://localhost:5001')
    username = data.get('username', 'admin')
    password = data.get('password', '')

    from .spiderfoot_service import SpiderFootConfig, SpiderFootService

    config = SpiderFootConfig(
        base_url=url, username=username, password=password)
    service = SpiderFootService(config)

    if service.is_available():
        info = service.get_server_info()
        return jsonify({
            'success': True,
            'message': 'Connection successful',
            'server_info': info
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Could not connect to SpiderFoot server'
        }), 400


@cms_bp.route('/api/spiderfoot/status')
@login_required
@roles_required('admin', 'senior_investigator')
def api_spiderfoot_status():
    """Get SpiderFoot server status."""
    sf_service = get_spiderfoot_service()
    available = sf_service.is_available()
    info = sf_service.get_server_info() if available else None

    # Count scans
    scan_counts = {
        'total': SpiderFootScan.query.filter_by(is_deleted=False).count(),
        'running': SpiderFootScan.query.filter_by(status='running', is_deleted=False).count(),
        'completed': SpiderFootScan.query.filter_by(status='completed', is_deleted=False).count(),
    }

    return jsonify({
        'available': available,
        'server_info': info,
        'scan_counts': scan_counts
    })


@cms_bp.route('/spiderfoot/subject/<subject_id>/scan', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def spiderfoot_scan_subject(subject_id: str):
    """Scan a subject with SpiderFoot."""
    subject = Subject.query.get_or_404(subject_id)
    subject.decrypt_identifiers()

    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form

        profile = data.get('profile', 'basic')
        use_case = data.get('use_case', 'passive')
        case_id = data.get('case_id')

        # Try to determine target from subject
        from .spiderfoot_service import ScanTarget

        target_info = ScanTarget.from_subject(subject.to_dict())

        if not target_info:
            if request.is_json:
                return jsonify({'error': 'Could not determine scan target from subject'}), 400
            flash('Could not determine scan target from subject type.', 'error')
            return redirect(url_for('cms.view_subject', subject_id=subject_id))

        sf_service = get_spiderfoot_service()

        if not sf_service.is_available():
            if request.is_json:
                return jsonify({'error': 'SpiderFoot server is not available'}), 503
            flash('SpiderFoot server is not available.', 'error')
            return redirect(url_for('cms.spiderfoot_index'))

        # Start scan
        result = sf_service.start_scan(
            target=target_info.value,
            target_type=target_info.target_type,
            scan_name=f"Scan - {subject.name}",
            use_case=use_case,
            profile=profile
        )

        if not result or not result.get('scan_id'):
            if request.is_json:
                return jsonify({'error': 'Failed to start scan'}), 500
            flash('Failed to start SpiderFoot scan.', 'error')
            return redirect(url_for('cms.spiderfoot_index'))

        # Create scan record
        scan_record = SpiderFootScan(
            scan_id=result['scan_id'],
            scan_name=f"Scan - {subject.name}",
            target_value=target_info.value,
            target_type=target_info.target_type,
            case_id=case_id,
            subject_id=subject_id,
            use_case=use_case,
            profile=profile,
            module_ids=SpiderFootService.INVESTIGATION_PROFILES.get(profile, {}).get(
                'modules', []) if (SpiderFootService and profile) else [],
            status='running',
            created_by=current_user.id
        )
        scan_record.update_status('running', 0)

        db.session.add(scan_record)

        AuditLog.log(
            user_id=current_user.id,
            action='spiderfoot_scan_start',
            entity_type='spiderfoot_scan',
            entity_id=scan_record.id,
            ip_address=request.remote_addr,
            case_id=case_id,
            subject_id=subject_id,
            description=f"Started SpiderFoot scan for subject: {subject.name}"
        )
        db.session.commit()

        if request.is_json:
            return jsonify({
                'message': 'Scan started',
                'scan': scan_record.to_dict()
            }), 201

        flash('SpiderFoot scan started.', 'success')
        return redirect(url_for('cms.spiderfoot_scan_status', scan_id=scan_record.id))

    # GET - Show scan form with subject info
    cases = Case.query.filter_by(is_deleted=False).order_by(
        Case.case_number.desc()).all()

    return render_template('cms/spiderfoot/scan_subject.html',
                           subject=subject,
                           cases=cases,
                           profiles=SpiderFootService.INVESTIGATION_PROFILES if SpiderFootService else {},
                           use_cases=SpiderFootService.USE_CASES if SpiderFootService else {}
                           )
