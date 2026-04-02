"""
Authentication and Authorization Utilities
==========================================
Role-Based Access Control (RBAC) implementation for CMS.

Design Decisions:
- Decorator-based approach for clean route definitions
- Role hierarchy: Admin > Senior > Junior > Viewer
- Case-level access checks for investigators
- Mandatory audit logging for compliance
"""

import functools
import logging
from datetime import datetime
from typing import Callable, List, Optional, Union

from flask import (
    Blueprint, request, jsonify, render_template, 
    redirect, url_for, flash, session, g, current_app
)
from flask_login import (
    LoginManager, login_user, logout_user, 
    login_required, current_user
)

from .models import db, User, AuditLog, Case, Client, Subject
from .encryption_utils import encryptor


logger = logging.getLogger(__name__)


# =============================================================================
# Login Manager Setup
# =============================================================================

login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    """Load user by ID for Flask-Login."""
    return User.query.get(user_id)


@login_manager.unauthorized_handler
def unauthorized():
    """Handle unauthorized access."""
    if request.is_json:
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('cms.login'))


# =============================================================================
# RBAC Decorators
# =============================================================================

def roles_required(*allowed_roles: str):
    """
    Decorator to restrict access to users with specific roles.
    
    Usage:
        @roles_required('admin', 'senior_investigator')
        def sensitive_route():
            ...
    
    Args:
        allowed_roles: List of role names that can access this route.
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return unauthorized()
            
            if not current_user.is_active:
                return jsonify({'error': 'Account is disabled'}), 403
            
            # Normalize roles
            normalized_roles = []
            for role in allowed_roles:
                if hasattr(role, 'value'):
                    normalized_roles.append(role.value)
                else:
                    normalized_roles.append(role)
            
            if current_user.role not in normalized_roles:
                logger.warning(
                    f"Access denied: User {current_user.username} with role "
                    f"{current_user.role} attempted to access resource requiring "
                    f"roles: {normalized_roles}"
                )
                
                # Log unauthorized access attempt
                AuditLog.log(
                    user_id=current_user.id,
                    action='access_denied',
                    entity_type='route',
                    entity_id=request.endpoint,
                    ip_address=request.remote_addr,
                    user_agent=request.user_agent.string,
                    description=f"Unauthorized access attempt to {request.endpoint}"
                )
                db.session.commit()
                
                if request.is_json:
                    return jsonify({'error': 'Insufficient permissions'}), 403
                flash('You do not have permission to access this resource.', 'danger')
                return redirect(url_for('cms.dashboard'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f: Callable) -> Callable:
    """Decorator for admin-only routes."""
    return roles_required('admin')(f)


def senior_required(f: Callable) -> Callable:
    """Decorator for routes requiring senior investigator or higher."""
    return roles_required('admin', 'senior_investigator')(f)


def investigator_required(f: Callable) -> Callable:
    """Decorator for routes requiring any investigator role."""
    return roles_required('admin', 'senior_investigator', 'junior_investigator')(f)


def can_export(f: Callable) -> Callable:
    """Decorator to restrict data export to senior investigators only."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.can_export:
            AuditLog.log(
                user_id=current_user.id,
                action='export_denied',
                entity_type='export',
                ip_address=request.remote_addr,
                description=f"Export denied for user with role {current_user.role}"
            )
            db.session.commit()
            
            if request.is_json:
                return jsonify({'error': 'Export not permitted for your role'}), 403
            flash('Data export is not permitted for your role.', 'warning')
            return redirect(url_for('cms.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def case_access_required(f: Callable) -> Callable:
    """
    Decorator to check case-level access permissions.
    
    Expects 'case_id' in route parameters or request data.
    Checks if user is assigned to the case or is an admin.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return unauthorized()
        
        # Get case_id from various sources
        case_id = kwargs.get('case_id')
        if not case_id and request.is_json:
            data = request.get_json()
            case_id = data.get('case_id') if data else None
        if not case_id:
            case_id = request.args.get('case_id')
        
        if case_id:
            case = Case.query.get(case_id)
            if case and not current_user.can_access_case(case):
                logger.warning(
                    f"Case access denied: User {current_user.username} "
                    f"attempted to access case {case_id}"
                )
                
                AuditLog.log(
                    user_id=current_user.id,
                    action='case_access_denied',
                    entity_type='case',
                    entity_id=case_id,
                    ip_address=request.remote_addr,
                    description=f"Unauthorized case access attempt"
                )
                db.session.commit()
                
                if request.is_json:
                    return jsonify({'error': 'No access to this case'}), 403
                flash('You do not have access to this case.', 'warning')
                return redirect(url_for('cms.cases'))
        
        return f(*args, **kwargs)
    return decorated_function


def audit_log(action: str, entity_type: str, get_entity_id: Callable = None):
    """
    Decorator to automatically log actions to audit trail.
    
    Usage:
        @audit_log('create', 'case')
        def create_case():
            ...
    
    Args:
        action: The action type (create, update, delete, read, export)
        entity_type: The type of entity being accessed
        get_entity_id: Optional function to extract entity_id from response
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            # Execute the route
            response = f(*args, **kwargs)
            
            # Extract entity_id if provided
            entity_id = None
            if get_entity_id:
                try:
                    entity_id = get_entity_id(response)
                except:
                    pass
            
            # Log the action
            AuditLog.log(
                user_id=current_user.id if current_user.is_authenticated else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string,
                description=f"{action.upper()} {entity_type}"
            )
            db.session.commit()
            
            return response
        return decorated_function
    return decorator


# =============================================================================
# Authentication Routes
# =============================================================================

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login page."""
    if current_user.is_authenticated:
        return redirect(url_for('cms.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        if not username or not password:
            flash('Please enter username and password.', 'warning')
            return render_template('cms/login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been disabled. Contact an administrator.', 'danger')
                return render_template('cms/login.html')
            
            # Update last login
            user.last_login = datetime.utcnow()
            
            # Log successful login
            AuditLog.log(
                user_id=user.id,
                action='login',
                entity_type='user',
                entity_id=user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string,
                description=f"User {username} logged in"
            )
            db.session.commit()
            
            login_user(user, remember=remember)
            
            # Redirect to intended page or dashboard
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('cms.dashboard'))
        
        # Log failed login attempt
        AuditLog.log(
            user_id=None,
            action='login_failed',
            entity_type='user',
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
            description=f"Failed login attempt for username: {username}"
        )
        db.session.commit()
        
        flash('Invalid username or password.', 'danger')
    
    return render_template('cms/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout."""
    AuditLog.log(
        user_id=current_user.id,
        action='logout',
        entity_type='user',
        entity_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string,
        description=f"User {current_user.username} logged out"
    )
    db.session.commit()
    
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# =============================================================================
# User Management Routes
# =============================================================================

users_bp = Blueprint('users', __name__, url_prefix='/users')


@users_bp.route('/')
@login_required
@roles_required('admin', 'senior_investigator')
def list_users():
    """List all users."""
    users = User.query.filter_by(is_active=True).all()
    return render_template('cms/users/list.html', users=users)


@users_bp.route('/<user_id>')
@login_required
def view_user(user_id: str):
    """View user details."""
    # Users can view their own profile, admins can view anyone
    if user_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    user = User.query.get_or_404(user_id)
    return render_template('cms/users/view.html', user=user)


@users_bp.route('/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    """Create a new user (admin only)."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        
        # Validate required fields
        required = ['username', 'email', 'password', 'full_name', 'role']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        # Check for duplicate username/email
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'error': 'Username already exists'}), 400
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'error': 'Email already exists'}), 400
        
        # Validate role
        valid_roles = ['admin', 'senior_investigator', 'junior_investigator', 'viewer']
        if data['role'] not in valid_roles:
            return jsonify({'error': 'Invalid role'}), 400
        
        # Create user
        user = User(
            username=data['username'],
            email=data['email'],
            full_name=data['full_name'],
            role=data['role']
        )
        user.set_password(data['password'])
        
        db.session.add(user)
        
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='user',
            entity_id=user.id,
            ip_address=request.remote_addr,
            new_values={'username': user.username, 'role': user.role},
            description=f"Created user {user.username} with role {user.role}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'User created', 'user': user.to_dict()}), 201
        
        flash(f'User {user.username} created successfully.', 'success')
        return redirect(url_for('users.list_users'))
    
    return render_template('cms/users/create.html')


@users_bp.route('/<user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id: str):
    """Edit user details."""
    # Users can edit their own profile, admins can edit anyone
    if user_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        old_values = {}
        changes = {}
        
        # Fields that can be edited by the user themselves
        user_editable = ['full_name', 'email']
        # Fields only editable by admins
        admin_editable = ['role', 'is_active']
        
        for field in user_editable:
            if field in data and getattr(user, field) != data[field]:
                old_values[field] = getattr(user, field)
                changes[field] = {'old': old_values[field], 'new': data[field]}
                setattr(user, field, data[field])
        
        # Admin-only fields
        if current_user.is_admin:
            for field in admin_editable:
                if field in data and getattr(user, field) != data[field]:
                    old_values[field] = getattr(user, field)
                    changes[field] = {'old': old_values[field], 'new': data[field]}
                    setattr(user, field, data[field])
            
            # Password change (admin can set without old password)
            if data.get('password'):
                user.set_password(data['password'])
                changes['password'] = {'new': '***'}
        
        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='user',
            entity_id=user.id,
            changes_made=changes if changes else None,
            ip_address=request.remote_addr,
            description=f"Updated user {user.username}"
        )
        db.session.commit()
        
        if request.is_json:
            return jsonify({'message': 'User updated', 'user': user.to_dict()})
        
        flash('User updated successfully.', 'success')
        return redirect(url_for('users.view_user', user_id=user.id))
    
    return render_template('cms/users/edit.html', user=user)


@users_bp.route('/<user_id>/deactivate', methods=['POST'])
@login_required
@admin_required
def deactivate_user(user_id: str):
    """Deactivate a user account."""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        return jsonify({'error': 'Cannot deactivate your own account'}), 400
    
    user.is_active = False
    
    AuditLog.log(
        user_id=current_user.id,
        action='deactivate',
        entity_type='user',
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Deactivated user {user.username}"
    )
    db.session.commit()
    
    flash(f'User {user.username} has been deactivated.', 'info')
    
    if request.is_json:
        return jsonify({'message': 'User deactivated'})
    return redirect(url_for('users.list_users'))


# =============================================================================
# Helper Functions
# =============================================================================

def get_client_ip() -> str:
    """Get client IP address, handling proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def require_api_key(f: Callable) -> Callable:
    """
    Decorator for API routes that require an API key.
    Used for programmatic access (e.g., from OSINT tools).
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        
        if not api_key:
            return jsonify({'error': 'API key required'}), 401
        
        # Validate API key (in production, this should check a database)
        expected_key = current_app.config.get('CMS_API_KEY')
        if not expected_key or api_key != expected_key:
            return jsonify({'error': 'Invalid API key'}), 403
        
        return f(*args, **kwargs)
    return decorated_function
