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
from datetime import datetime, timezone
from collections.abc import Callable

import flask

from flask import (
    Blueprint,
    request,
    jsonify,
    redirect,
    url_for,
    flash,
    current_app,
    abort,
    g,
)
from flask_login import (
    LoginManager,
    current_user,
)

from .models import db, User, ApiKey, AuditLog, Case, Subject


logger = logging.getLogger(__name__)

MAX_LOGIN_ATTEMPTS = 5
ACCOUNT_LOCKOUT_MINUTES = 15


# =============================================================================
# Login Manager Setup
# =============================================================================

login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Load user by ID for Flask-Login."""
    return db.session.get(User, user_id)


@login_manager.request_loader
def load_user_from_request(request: flask.Request) -> User | None:
    """Load user from X-API-Key header for non-session REST access."""
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        return None
    prefix = api_key[:8] if len(api_key) >= 8 else api_key
    key_record = ApiKey.query.filter_by(key_prefix=prefix, is_active=True).first()
    if not key_record or not key_record.verify_key(api_key):
        return None
    key_record.last_used_at = datetime.now(timezone.utc)
    db.session.commit()
    user = db.session.get(User, key_record.user_id)
    if user:
        # Store API key scopes in flask.g for scope checking
        g.api_key_scopes = key_record.scopes or ["read"]
        g.authenticated_via_api_key = True
    return user


@login_manager.unauthorized_handler
def unauthorized() -> flask.Response:
    """Handle unauthorized access."""
    if request.is_json:
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("auth.login"))


# =============================================================================
# RBAC Decorators
# =============================================================================


def tenant_owner_required(f: Callable) -> Callable:
    """
    Decorator to restrict access to tenant owners (or super admins).

    Usage:
        @tenant_owner_required
        def tenant_settings():
            ...
    """

    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_super_admin and not current_user.is_tenant_owner:
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def roles_required(*allowed_roles: str) -> Callable:
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
                return jsonify({"error": "Account is disabled"}), 403

            # Normalize roles
            normalized_roles = []
            for role in allowed_roles:
                if hasattr(role, "value"):
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
                    action="access_denied",
                    entity_type="route",
                    entity_id=request.endpoint,
                    ip_address=request.remote_addr,
                    user_agent=request.user_agent.string,
                    description=f"Unauthorized access attempt to {request.endpoint}",
                )
                db.session.commit()

                if request.is_json:
                    return jsonify({"error": "Insufficient permissions"}), 403
                flash("You do not have permission to access this resource.", "danger")
                return redirect(url_for("cms.dashboard"))

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def admin_required(f: Callable) -> Callable:
    """Decorator for admin-only routes."""
    return roles_required("admin", "owner")(f)


def senior_required(f: Callable) -> Callable:
    """Decorator for routes requiring senior investigator or higher."""
    return roles_required("admin", "owner", "senior_investigator")(f)


def investigator_required(f: Callable) -> Callable:
    """Decorator for routes requiring any investigator role."""
    return roles_required(
        "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
    )(f)


def require_scope(*scopes: str) -> Callable:
    """
    Decorator for API routes that require specific API key scopes.

    Works with both session-based auth (full access) and API-key auth (scope-checked).
    Session-authenticated users bypass scope checks.

    Usage:
        @require_scope('read')
        @require_scope('read', 'write')
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return unauthorized()

            # Session-authenticated users have full access
            if not getattr(g, "authenticated_via_api_key", False):
                return f(*args, **kwargs)

            # API-key authenticated: check scopes
            key_scopes = set(getattr(g, "api_key_scopes", ["read"]))
            if not any(s in key_scopes for s in scopes):
                return jsonify(
                    {
                        "error": "Insufficient API key scope. Required one of: "
                        + ", ".join(scopes)
                    }
                ), 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def can_export(f: Callable) -> Callable:
    """Decorator to restrict data export to senior investigators only."""

    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.can_export:
            AuditLog.log(
                user_id=current_user.id,
                action="export_denied",
                entity_type="export",
                ip_address=request.remote_addr,
                description=f"Export denied for user with role {current_user.role}",
            )
            db.session.commit()

            if request.is_json:
                return jsonify({"error": "Export not permitted for your role"}), 403
            flash("Data export is not permitted for your role.", "warning")
            return redirect(url_for("cms.dashboard"))
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
        case_id = kwargs.get("case_id")
        if not case_id and request.is_json:
            data = request.get_json()
            case_id = data.get("case_id") if data else None
        if not case_id:
            case_id = request.args.get("case_id")

        if case_id:
            case = db.session.get(Case, case_id)

            # Tenant isolation: non-super-admin must match tenant
            if (
                case
                and not current_user.is_super_admin
                and case.tenant_id != current_user.tenant_id
            ):
                logger.warning(
                    f"Case tenant mismatch: User {current_user.username} (tenant={current_user.tenant_id}) "
                    f"tried to access case {case_id} (tenant={case.tenant_id})"
                )
                if request.is_json:
                    return jsonify({"error": "No access to this case"}), 403
                flash("You do not have access to this case.", "warning")
                return redirect(url_for("cms.cases"))

            if case and not current_user.can_access_case(case):
                logger.warning(
                    f"Case access denied: User {current_user.username} "
                    f"attempted to access case {case_id}"
                )

                AuditLog.log(
                    user_id=current_user.id,
                    action="case_access_denied",
                    entity_type="case",
                    entity_id=case_id,
                    ip_address=request.remote_addr,
                    description="Unauthorized case access attempt",
                )
                db.session.commit()

                if request.is_json:
                    return jsonify({"error": "No access to this case"}), 403
                flash("You do not have access to this case.", "warning")
                return redirect(url_for("cms.cases"))

        return f(*args, **kwargs)

    return decorated_function


def case_edit_required(f: Callable) -> Callable:
    """
    Decorator to check if user can edit a case.

    - Admins can edit everything
    - Senior investigators can edit active/in-progress cases
    - Junior investigators can add findings but not modify case details
    - Finding operations are always allowed (even on closed cases)
    - Closed/archived cases are always read-only for case modifications

    Use after @case_access_required decorator.
    """

    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return unauthorized()

        case_id = kwargs.get("case_id")
        if not case_id:
            case_id = request.args.get("case_id")

        if not case_id and request.is_json:
            data = request.get_json()
            case_id = data.get("case_id") if data else None

        if not case_id:
            return f(*args, **kwargs)  # No case to check

        case = db.session.get(Case, case_id)
        if not case:
            return f(*args, **kwargs)  # Let the route handle 404

        # Admins can edit everything
        if current_user.is_admin:
            return f(*args, **kwargs)

        # Finding operations are always allowed (investigative work)
        endpoint = request.endpoint or ""
        if "finding" in endpoint or "/findings/" in endpoint or "osint" in endpoint:
            return f(*args, **kwargs)

        # Status transitions are allowed on closed/archived cases
        if "transition" in endpoint:
            return f(*args, **kwargs)

        # Closed/archived cases are always read-only for case modifications
        if case.status in ["closed", "archived"]:
            logger.warning(
                f"Edit denied: User {current_user.username} attempted to edit "
                f"closed/archived case {case_id}"
            )
            AuditLog.log(
                user_id=current_user.id,
                action="case_edit_denied",
                entity_type="case",
                entity_id=case_id,
                ip_address=request.remote_addr,
                description=f"Attempted to edit closed/archived case {case.case_number}",
            )
            db.session.commit()

            if request.is_json:
                return jsonify(
                    {"error": "This case is closed and cannot be edited"}
                ), 403
            flash("This case is closed and cannot be edited.", "warning")
            return redirect(url_for("cms.view_case", case_id=case_id))

        # Junior investigators and investigators can only add findings
        if current_user.role in ("junior_investigator", "investigator"):
            logger.warning(
                f"Edit denied: {current_user.role} {current_user.username} attempted "
                f"to edit case {case_id}"
            )

            if request.is_json:
                return jsonify(
                    {"error": "Junior investigators cannot edit case details"}
                ), 403
            flash("Junior investigators cannot edit case details.", "warning")
            return redirect(url_for("cms.view_case", case_id=case_id))

        # Senior investigators can edit active cases
        if current_user.is_senior:
            return f(*args, **kwargs)

        return f(*args, **kwargs)

    return decorated_function


def ensure_tenant_access(entity) -> None:
    """Abort with 403 if non-super-admin user does not own this entity's tenant."""
    if (
        not current_user.is_super_admin
        and hasattr(entity, "tenant_id")
        and entity.tenant_id != current_user.tenant_id
    ):
        logger.warning(
            f"Tenant access denied: User {current_user.username} (tenant={current_user.tenant_id}) "
            f"attempted to access {type(entity).__name__} {getattr(entity, 'id', 'unknown')} "
            f"(tenant={entity.tenant_id})"
        )
        abort(403)


def subject_access_required(f: Callable) -> Callable:
    """
    Decorator to check subject-level access.

    Users can only access subjects linked to cases they have access to.
    Admins bypass this check.
    """

    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return unauthorized()

        subject_id = kwargs.get("subject_id")
        if not subject_id:
            return f(*args, **kwargs)

        subject = db.session.get(Subject, subject_id)
        if not subject:
            return f(*args, **kwargs)

        # Tenant isolation: non-super-admin must match tenant
        if (
            not current_user.is_super_admin
            and subject.tenant_id != current_user.tenant_id
        ):
            logger.warning(
                f"Subject tenant mismatch: User {current_user.username} (tenant={current_user.tenant_id}) "
                f"tried to access subject {subject_id} (tenant={subject.tenant_id})"
            )
            if request.is_json:
                return jsonify({"error": "No access to this subject"}), 403
            flash("You do not have access to this subject.", "warning")
            return redirect(url_for("cms.subjects"))

        # Same-tenant admin bypasses case-linkage check
        if current_user.is_admin:
            return f(*args, **kwargs)

        from .models import case_subjects

        linked_case_ids = [
            row.case_id
            for row in db.session.query(case_subjects.c.case_id)
            .filter(case_subjects.c.subject_id == subject_id)
            .all()
        ]

        if not linked_case_ids:
            if request.is_json:
                return jsonify({"error": "No access to this subject"}), 403
            flash("You do not have access to this subject.", "warning")
            return redirect(url_for("cms.subjects"))

        has_access = False
        for cid in linked_case_ids:
            case = db.session.get(Case, cid)
            if case and current_user.can_access_case(case):
                has_access = True
                break

        if not has_access:
            AuditLog.log(
                user_id=current_user.id,
                action="access_denied",
                entity_type="subject",
                entity_id=subject_id,
                ip_address=request.remote_addr,
                description="Subject access denied: no case access",
            )
            db.session.commit()
            if request.is_json:
                return jsonify({"error": "No access to this subject"}), 403
            flash("You do not have access to this subject.", "warning")
            return redirect(url_for("cms.subjects"))

        return f(*args, **kwargs)

    return decorated_function


def audit_read(entity_type: str) -> Callable:
    """
    Decorator to automatically log read/view actions to audit trail.

    Usage:
        @audit_read('subject')
        def view_subject(subject_id):
            ...

    Args:
        entity_type: The type of entity being viewed
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            response = f(*args, **kwargs)

            if current_user.is_authenticated:
                entity_id = None
                for key in ("subject_id", "case_id", "client_id", "user_id"):
                    if key in kwargs:
                        entity_id = kwargs[key]
                        break

                AuditLog.log(
                    user_id=current_user.id,
                    action="read",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    ip_address=request.remote_addr,
                    description=f"Viewed {entity_type}: {entity_id}",
                )
                db.session.commit()

            return response

        return decorated_function

    return decorator


def audit_log(
    action: str, entity_type: str, get_entity_id: Callable = None
) -> Callable:
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
                except Exception:
                    logger.warning("Failed to get entity ID from response")

            # Log the action
            AuditLog.log(
                user_id=current_user.id if current_user.is_authenticated else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string,
                description=f"{action.upper()} {entity_type}",
            )
            db.session.commit()

            return response

        return decorated_function

    return decorator


# =============================================================================
# Blueprint Definitions
# =============================================================================

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
users_bp = Blueprint("users", __name__, url_prefix="/users")

# Import routes to register them on the blueprints
from .routes import auth_routes  # noqa: F401


# =============================================================================
# Helper Functions
# =============================================================================
# =============================================================================
# Helper Functions
# =============================================================================


def apply_tenant_filter(query, model):
    """Apply tenant isolation filter to a SQLAlchemy query.

    For non-super-admin users, adds ``model.tenant_id == current_user.tenant_id``
    to the WHERE clause.  Super admins see all tenants (no filter).

    This is the SQLite-compatible counterpart of PostgreSQL RLS — always add this
    to every ``Model.query`` in list/index routes.
    """
    if not current_user.is_super_admin and g.get("tenant_id"):
        return query.filter(model.tenant_id == g.tenant_id)
    return query


def get_client_ip() -> str:
    """Get client IP address, handling proxies."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr


def get_accessible_case_ids(user) -> list[str]:
    """
    Return all case IDs the user can access within their tenant.
    Replicates can_access_case() logic but in bulk.
    """
    from .models import Case, case_assignments

    tid = user.tenant_id

    # Admin bypass — returns all non-deleted case IDs within tenant
    if user.is_admin:
        return [
            r[0]
            for r in Case.query.with_entities(Case.id)
            .filter(Case.is_deleted == False, Case.tenant_id == tid)
            .all()
        ]

    # Build list of case IDs via all access patterns
    direct = (
        Case.query.with_entities(Case.id)
        .filter(
            Case.is_deleted == False,
            Case.tenant_id == tid,
            db.or_(
                Case.created_by == user.id,
                Case.lead_investigator_id == user.id,
                Case.assigned_to == user.id,
            ),
        )
        .all()
    )
    direct_ids = {r[0] for r in direct}

    # Via case_assignments table
    assigned_ids = {
        r[0]
        for r in db.session.query(case_assignments.c.case_id)
        .filter(case_assignments.c.user_id == user.id)
        .all()
    }

    return list(direct_ids | assigned_ids)


def require_api_key(f: Callable) -> Callable:
    """
    Decorator for API routes that require an API key.
    Used for programmatic access (e.g., from OSINT tools).
    """

    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")

        if not api_key:
            return jsonify({"error": "API key required"}), 401

        # Validate API key (in production, this should check a database)
        expected_key = current_app.config.get("CMS_API_KEY")
        if not expected_key or api_key != expected_key:
            return jsonify({"error": "Invalid API key"}), 403

        return f(*args, **kwargs)

    return decorated_function
