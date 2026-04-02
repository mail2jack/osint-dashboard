"""
Case Management System - Routes
================================
CRUD operations for all CMS entities with RBAC and audit logging.

Design Decisions:
- RESTful API design with both JSON API and HTML views
- All write operations are automatically audited
- Case-level permissions for investigators
- Soft deletes to preserve data for legal compliance
"""

import logging
from datetime import datetime, date
from typing import Optional
from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, flash, current_app
)
from flask_login import login_required, current_user

from .models import (
    db, Case, Client, Subject, Finding, FinancialRecord,
    AuditLog, Document, User, CaseStatus, CasePriority,
    SubjectType, VerificationStatus
)
from .auth import (
    roles_required, admin_required, senior_required,
    investigator_required, can_export, case_access_required
)
from .encryption_utils import encryptor


logger = logging.getLogger(__name__)

cms_bp = Blueprint('cms', __name__, url_prefix='/cms')


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
        my_cases = Case.query.join(
            db.session.execute(
                db.text("SELECT case_id FROM case_assignments WHERE user_id = :user_id")
            ).params(user_id=current_user.id).fetchall(),
            Case.id == db.text('case_id')
        ).filter(
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
            Case.is_deleted == False
        ).order_by(Case.updated_at.desc()).limit(10).all()
    
    # Recent activity
    recent_activity = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).limit(20).all()
    
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
            changes_made=changes,
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
    assigned = request.args.get('assigned', '')
    
    query = Case.query.filter_by(is_deleted=False)
    
    # Filter by status
    if status:
        query = query.filter_by(status=status)
    
    # Filter by priority
    if priority:
        query = query.filter_by(priority=priority)
    
    # Search in title and case number
    if search:
        query = query.filter(
            db.or_(
                Case.title.ilike(f'%{search}%'),
                Case.case_number.ilike(f'%{search}%')
            )
        )
    
    # Filter by assignment (non-admins see only assigned cases)
    if assigned == 'me' and not current_user.is_admin:
        query = query.filter(
            db.or_(
                Case.assigned_to == current_user.id,
                Case.id.in_([
                    a.case_id for a in 
                    db.session.query(db.text('case_id')).select_from('case_assignments').where(
                        db.text('user_id = :uid')
                    ).params(uid=current_user.id).all()
                ])
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
        filters={'status': status, 'priority': priority, 'search': search, 'assigned': assigned}
    )


@cms_bp.route('/cases/<case_id>')
@login_required
@case_access_required
def view_case(case_id: str):
    """View case details with subjects, findings, and financials."""
    case = Case.query.get_or_404(case_id)
    subjects = case.subjects.all()
    findings = case.findings.filter_by(is_deleted=False).order_by(Finding.created_at.desc()).all()
    financials = case.financial_records.filter_by(is_deleted=False).order_by(FinancialRecord.transaction_date.desc()).all()
    
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
        findings=findings,
        financials=financials
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
def edit_case(case_id: str):
    """Edit case details."""
    case = Case.query.get_or_404(case_id)
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        changes = {}
        
        editable_fields = ['title', 'description', 'priority', 'target_end_date',
                         'case_type', 'jurisdiction', 'tags']
        
        for field in editable_fields:
            if field in data:
                old_value = getattr(case, field)
                new_value = data[field]
                if new_value != old_value:
                    changes[field] = {'old': str(old_value) if old_value else None, 'new': str(new_value)}
                    setattr(case, field, new_value)
        
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
            changes_made=changes,
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
def transition_case(case_id: str):
    """Transition case to a new status."""
    case = Case.query.get_or_404(case_id)
    data = request.get_json() if request.is_json else request.form
    
    new_status = data.get('status')
    if not new_status:
        return jsonify({'error': 'Status is required'}), 400
    
    old_status = case.status
    
    if not case.transition_status(new_status, current_user.id):
        return jsonify({
            'error': f'Cannot transition from {old_status} to {new_status}'
        }), 400
    
    AuditLog.log(
        user_id=current_user.id,
        action='status_change',
        entity_type='case',
        entity_id=case_id,
        changes_made={'status': {'old': old_status, 'new': new_status}},
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
    
    query = Subject.query.filter_by(is_deleted=False)
    
    if search:
        query = query.filter(Subject.name.ilike(f'%{search}%'))
    
    if subject_type:
        query = query.filter_by(subject_type=subject_type)
    
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
        findings=findings
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
        return redirect(url_for('cms.view_subject', subject_id=subject.id))
    
    return render_template('cms/subjects/create.html')


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
            changes_made=changes,
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
