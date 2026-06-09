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
import hashlib
import io
import json
import base64
import secrets
import logging
from datetime import datetime, timezone
from collections.abc import Callable

import flask
import pyotp
import qrcode

from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    session,
    current_app,
    abort,
    g,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)

from .models import db, User, ApiKey, AuditLog, Case, Subject, Tenant
from .validation import validate
from .rate_limiting import is_rate_limited, rate_limit, rate_limit_after_n
from .notifications import (
    notify_login_failed,
    notify_login_success,
    notify_account_locked,
    notify_user_created,
)
from .validation import (
    LoginSchema,
    SetPasswordSchema,
    CreateUserSchema,
    EditUserSchema,
    ChangePasswordSchema,
)
from .geo_utils import log_login_attempt


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
    return roles_required("admin")(f)


def senior_required(f: Callable) -> Callable:
    """Decorator for routes requiring senior investigator or higher."""
    return roles_required("admin", "senior_investigator")(f)


def investigator_required(f: Callable) -> Callable:
    """Decorator for routes requiring any investigator role."""
    return roles_required(
        "admin", "senior_investigator", "investigator", "junior_investigator"
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

        if current_user.is_admin:
            return f(*args, **kwargs)

        subject = db.session.get(Subject, subject_id)
        if not subject:
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
# Authentication Routes
# =============================================================================

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login() -> flask.Response:
    """User login page."""
    if current_user.is_authenticated:
        return redirect(url_for("cms.dashboard"))

    if request.method == "POST":
        # IP-based rate limiting
        rate_key = f"login_ip:{request.remote_addr or 'unknown'}"
        limited, _ = is_rate_limited(rate_key)
        if limited:
            flash(
                "Too many login attempts from this IP. Please wait before trying again.",
                "danger",
            )
            return render_template("cms/login.html")

        d = request.form.to_dict() if request.form else {}
        try:
            validated = LoginSchema(**d)
        except Exception:
            flash("Please enter username and password.", "warning")
            return render_template("cms/login.html")
        username = validated.username
        password = validated.password
        remember = validated.remember or False

        if not username or not password:
            flash("Please enter username and password.", "warning")
            return render_template("cms/login.html")

        user = User.query.filter_by(username=username).first()

        if (
            user
            and user.locked_until
            and user.locked_until > datetime.now(timezone.utc).replace(tzinfo=None)
        ):
            remaining = int(
                (
                    user.locked_until - datetime.now(timezone.utc).replace(tzinfo=None)
                ).total_seconds()
                / 60
            )
            flash(
                f"Account locked due to too many failed attempts. Try again in {remaining} minutes.",
                "danger",
            )
            return render_template("cms/login.html")

        if user and user.check_password(password):
            # Reset failed attempts on successful login
            user.failed_login_attempts = 0
            user.locked_until = None

            if not user.is_active:
                flash(
                    "Your account has been disabled. Contact an administrator.",
                    "danger",
                )
                return render_template("cms/login.html")

            tenant = db.session.get(Tenant, user.tenant_id)
            if not tenant or not tenant.is_active:
                flash(
                    "Your organization's account has been disabled. Contact support.",
                    "danger",
                )
                return render_template("cms/login.html")

            # Log successful password verification
            AuditLog.log(
                user_id=user.id,
                action="password_verified",
                entity_type="user",
                entity_id=user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string,
                description=f"Password verified for user {username}",
            )
            db.session.commit()

            # Notify login success
            notify_login_success(username, request.remote_addr)

            # 2FA is mandatory — require verification or first-time setup
            session["_2fa_user_id"] = user.id
            session["_2fa_remember"] = remember
            if user.totp_secret:
                return redirect(url_for("auth.verify_2fa"))
            return redirect(url_for("auth.setup_2fa"))

        # Increment failed login counter
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_LOGIN_ATTEMPTS:
                from datetime import timedelta

                user.locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=ACCOUNT_LOCKOUT_MINUTES
                )
                # Notify account locked
                notify_account_locked(
                    username, request.remote_addr, ACCOUNT_LOCKOUT_MINUTES
                )
        db.session.commit()

        # Notify login failed
        notify_login_failed(username, request.remote_addr)

        # Log failed login attempt (if user exists)
        if user:
            log_login_attempt(
                user_id=user.id,
                ip_address=request.remote_addr or "",
                is_success=False,
                user_agent=request.user_agent.string or "",
            )

        # IP rate limit on failed login (kicks in after 3 attempts, 15s wait)
        rate_limit_after_n(rate_key, max_attempts=3, retry_after=15)

        flash("Invalid username or password.", "danger")

    return render_template("cms/login.html")


@auth_bp.route("/logout")
@login_required
def logout() -> flask.Response:
    """User logout."""
    AuditLog.log(
        user_id=current_user.id,
        action="logout",
        entity_type="user",
        entity_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string,
        description=f"User {current_user.username} logged out",
    )
    db.session.commit()

    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/set-password/<token>", methods=["GET", "POST"])
def set_password(token) -> flask.Response:
    """Set/reset password using a token from email."""
    user = User.query.filter(User.password_reset_token.isnot(None)).all()
    user = next((u for u in user if u.verify_reset_token(token)), None)
    if not user:
        flash("Invalid or expired password reset link.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        rate_key = f"set_password_ip:{request.remote_addr or 'unknown'}"
        limited, _ = is_rate_limited(rate_key)
        if limited:
            flash("Too many attempts. Please wait before trying again.", "danger")
            return redirect(url_for("auth.login"))
        is_json = request.is_json
        raw = request.get_json() if is_json else request.form
        try:
            validated = SetPasswordSchema(**raw)
        except Exception:
            if is_json:
                return jsonify({"error": "Invalid input"}), 400
            flash("Invalid input. Please check your password.", "danger")
            return render_template("cms/set_password.html", token=token)
        password = validated.password
        confirm = validated.confirm_password

        if password != confirm:
            if is_json:
                return jsonify({"error": "Passwords do not match"}), 400
            flash("Passwords do not match.", "danger")
            return render_template("cms/set_password.html", token=token)

        user.set_password(password)
        user.clear_reset_token()
        db.session.commit()

        flash("Password set successfully. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("cms/set_password.html", token=token)


# =============================================================================
# 2FA (TOTP) Routes
# =============================================================================


@auth_bp.route("/2fa/verify", methods=["GET", "POST"])
def verify_2fa() -> flask.Response:
    """Verify TOTP code as second factor during login."""
    user_id = session.get("_2fa_user_id")
    if not user_id:
        if current_user.is_authenticated:
            return redirect(url_for("cms.dashboard"))
        flash("No pending 2FA verification. Please log in first.", "warning")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if not user or not user.totp_secret:
        session.pop("_2fa_user_id", None)
        session.pop("_2fa_remember", None)
        flash("2FA is not configured for this account. Please set it up.", "warning")
        return redirect(url_for("auth.setup_2fa"))

    if request.method == "POST":
        rate_key = f"verify_2fa_ip:{request.remote_addr or 'unknown'}"
        limited, _ = is_rate_limited(rate_key)
        if limited:
            flash("Too many attempts. Please wait before trying again.", "danger")
            return render_template("cms/2fa/verify.html")

        code = request.form.get("code", "").strip()
        recovery_code = request.form.get("recovery_code", "").strip()

        # Try TOTP code first
        totp = pyotp.TOTP(user.totp_secret)
        if code and totp.verify(code, valid_window=1):
            return _complete_2fa_login(user)

        # Try recovery code
        if recovery_code:
            codes = _get_backup_codes(user)
            for i, stored_hash in enumerate(codes):
                if secrets.compare_digest(
                    hashlib.sha256(recovery_code.encode()).hexdigest(), stored_hash
                ):
                    codes.pop(i)
                    user.backup_codes = json.dumps(codes)
                    db.session.commit()
                    flash("Recovery code used — please set up a new device.", "info")
                    return _complete_2fa_login(user)

        # 2FA IP rate limit (kicks in after 3 failed codes, 15s wait)
        rate_limit_after_n(rate_key, max_attempts=3, retry_after=15)

        # Log failed 2FA attempt
        if user_id:
            log_login_attempt(
                user_id=str(user_id),
                ip_address=request.remote_addr or "",
                is_success=False,
                user_agent=request.user_agent.string or "",
            )

        flash("Invalid code. Please try again.", "danger")

    return render_template("cms/2fa/verify.html")


def _complete_2fa_login(user) -> flask.Response:
    """Complete the second-factor login and clear the pending session."""
    user.last_login = datetime.now(timezone.utc)
    remember = session.pop("_2fa_remember", False)
    session.pop("_2fa_user_id", None)

    login_user(user, remember=remember)

    AuditLog.log(
        user_id=user.id,
        action="login",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string,
        description=f"User {user.username} logged in (2FA)",
    )
    db.session.commit()

    log_login_attempt(
        user_id=user.id,
        ip_address=request.remote_addr or "",
        is_success=True,
        user_agent=request.user_agent.string or "",
    )

    next_page = request.args.get("next") or session.pop("_2fa_next", None)
    if next_page and next_page.startswith("/") and not next_page.startswith("//"):
        return redirect(next_page)
    return redirect(url_for("cms.dashboard"))


@auth_bp.route("/2fa/setup", methods=["GET", "POST"])
def setup_2fa() -> flask.Response:
    """Set up TOTP two-factor authentication."""
    # Allow setup during login flow (partial auth via _2fa_user_id) or when fully logged in
    user_id = session.get("_2fa_user_id")
    user = db.session.get(User, user_id) if user_id else current_user

    if not user or not user.is_authenticated:
        flash("Please log in first.", "warning")
        return redirect(url_for("auth.login"))

    if user.totp_secret and not user_id:
        flash("2FA is already configured. Reset it first if needed.", "info")
        return redirect(url_for("users.view_user", user_id=user.id))

    partial_login = bool(user_id)

    if request.method == "POST":
        rate_key = f"setup_2fa_ip:{request.remote_addr or 'unknown'}"
        limited, _ = is_rate_limited(rate_key)
        if limited:
            flash("Too many attempts. Please wait before trying again.", "danger")
            return render_template(
                "cms/2fa/setup.html",
                partial_login=partial_login,
            )

        code = request.form.get("code", "").strip()
        secret = session.get("_2fa_pending_secret")

        if not secret:
            flash("Session expired. Please start again.", "warning")
            return redirect(url_for("auth.setup_2fa"))

        totp = pyotp.TOTP(secret)
        if not code or not totp.verify(code, valid_window=1):
            flash("Invalid code. Please try again.", "danger")
            return render_template(
                "cms/2fa/setup.html",
                secret=secret,
                partial_login=partial_login,
                provisioning_uri=totp.provisioning_uri(
                    user.username, issuer_name="CMS"
                ),
            )

        # Generate backup codes
        backup_codes = _generate_backup_codes()

        # Save to user
        user.totp_secret = secret
        user.totp_enabled = True
        user.backup_codes = json.dumps(
            [hashlib.sha256(c.encode()).hexdigest() for c in backup_codes]
        )
        db.session.commit()

        session.pop("_2fa_pending_secret", None)

        # Complete login before showing recovery codes (user must be authenticated)
        if partial_login:
            _complete_2fa_login(user)

        AuditLog.log(
            user_id=user.id,
            action="2fa_enabled",
            entity_type="user",
            entity_id=user.id,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
            description=f"User {user.username} enabled 2FA",
        )
        db.session.commit()

        return render_template(
            "cms/2fa/recovery_codes.html",
            codes=backup_codes,
            username=user.username,
            auto_close=partial_login,
        )

    # GET: generate secret and show QR
    secret = pyotp.random_base32()
    session["_2fa_pending_secret"] = secret
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(user.username, issuer_name="CMS")

    # Generate QR code as base64 PNG
    qr = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return render_template(
        "cms/2fa/setup.html",
        secret=secret,
        qr_b64=qr_b64,
        provisioning_uri=provisioning_uri,
        partial_login=partial_login,
    )


@auth_bp.route("/2fa/reset/<user_id>", methods=["POST"])
@login_required
@admin_required
def reset_2fa(user_id) -> flask.Response:
    """Admin: reset 2FA for another user (forces re-setup on next login)."""
    user = db.session.get(User, user_id) or abort(404)
    user.totp_secret = None
    user.totp_enabled = False
    user.backup_codes = None
    db.session.commit()

    AuditLog.log(
        user_id=current_user.id,
        action="2fa_reset",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Admin {current_user.username} reset 2FA for user {user.username}",
    )
    db.session.commit()

    flash(f"2FA reset for user {user.username}.", "success")
    return redirect(url_for("users.view_user", user_id=user.id))


# =============================================================================
# 2FA Helper Functions
# =============================================================================


def _get_backup_codes(user) -> list:
    """Return list of hashed backup codes from user model."""
    if not user.backup_codes:
        return []
    try:
        return json.loads(user.backup_codes)
    except (json.JSONDecodeError, TypeError):
        return []


def _generate_backup_codes(count: int = 8) -> list:
    """Generate count backup codes in XXXX-XXXX-XXXX format."""
    codes = []
    for _ in range(count):
        part1 = secrets.token_hex(2).upper()[:4]
        part2 = secrets.token_hex(2).upper()[:4]
        part3 = secrets.token_hex(2).upper()[:4]
        codes.append(f"{part1}-{part2}-{part3}")
    return codes


# =============================================================================
# User Management Routes
# =============================================================================

users_bp = Blueprint("users", __name__, url_prefix="/users")


@users_bp.route("/")
@login_required
@roles_required("admin", "senior_investigator")
def list_users() -> str:
    """List all users with pagination."""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    search = request.args.get("search", "")

    query = User.query.filter_by(is_active=True)

    # Tenant isolation: non-super-admin sees only their tenant's users
    if not current_user.is_super_admin:
        query = query.filter(User.tenant_id == current_user.tenant_id)

    if search:
        query = query.filter(
            db.or_(
                User.full_name.ilike(f"%{search}%"),
                User.username.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
            )
        )

    pagination = query.order_by(User.full_name).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        "cms/users/list.html",
        users=pagination.items,
        pagination=pagination,
        search=search,
    )


@users_bp.route("/<user_id>")
@login_required
def view_user(user_id: str) -> str:
    """View user details."""
    # Users can view their own profile, admins can view anyone
    if user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Access denied"}), 403

    user = db.session.get(User, user_id) or abort(404)
    return render_template("cms/users/view.html", user=user)


@users_bp.route("/<user_id>/activity")
@login_required
def user_activity(user_id: str) -> str:
    """View activity timeline for a specific user."""
    # Users can view their own activity, admins can view anyone
    if user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Access denied"}), 403

    user = db.session.get(User, user_id) or abort(404)

    # Get query parameters
    page = request.args.get("page", 1, type=int)
    per_page = 50
    entity_type = request.args.get("entity_type", "")
    action = request.args.get("action", "")

    # Build query
    query = AuditLog.query.filter_by(user_id=user_id)

    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    if action:
        query = query.filter_by(action=action)

    # Get activity counts by type
    activity_counts = (
        db.session.query(AuditLog.action, db.func.count(AuditLog.id))
        .filter(AuditLog.user_id == user_id)
        .group_by(AuditLog.action)
        .all()
    )

    entity_counts = (
        db.session.query(AuditLog.entity_type, db.func.count(AuditLog.id))
        .filter(AuditLog.user_id == user_id)
        .group_by(AuditLog.entity_type)
        .all()
    )

    # Pagination
    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Get recent cases worked on
    recent_case_ids = (
        db.session.query(AuditLog.case_id)
        .filter(AuditLog.user_id == user_id, AuditLog.case_id.isnot(None))
        .distinct()
        .limit(10)
        .all()
    )
    recent_case_ids = [c[0] for c in recent_case_ids]

    recent_cases = (
        Case.query.filter(Case.id.in_(recent_case_ids)).all() if recent_case_ids else []
    )

    # Get statistics
    total_actions = AuditLog.query.filter_by(user_id=user_id).count()
    today = datetime.now(timezone.utc).date()
    today_actions = AuditLog.query.filter(
        AuditLog.user_id == user_id, db.func.date(AuditLog.timestamp) == today
    ).count()

    # This week
    from datetime import timedelta

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    week_actions = AuditLog.query.filter(
        AuditLog.user_id == user_id, AuditLog.timestamp >= week_ago
    ).count()

    return render_template(
        "cms/users/activity.html",
        user=user,
        activities=pagination.items,
        pagination=pagination,
        activity_counts=activity_counts,
        entity_counts=entity_counts,
        recent_cases=recent_cases,
        stats={"total": total_actions, "today": today_actions, "week": week_actions},
        filters={"entity_type": entity_type, "action": action},
    )


@users_bp.route("/api/generate-password")
@login_required
@admin_required
def generate_password_api() -> flask.Response:
    """Generate a random password for the create user form."""
    import string

    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    password = "".join(secrets.choice(alphabet) for _ in range(16))
    return jsonify({"password": password})


@users_bp.route("/create", methods=["GET", "POST"])
@login_required
@admin_required
@rate_limit(limit=(30, 300), key_prefix="create_user")
def create_user() -> flask.Response:
    """Create a new user (admin only). Password is auto-generated by default."""
    generated_password = None

    if request.method == "GET":
        import string

        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        generated_password = "".join(secrets.choice(alphabet) for _ in range(16))
        tenants = (
            Tenant.query.order_by(Tenant.name).all()
            if current_user.is_super_admin
            else []
        )
        return render_template(
            "cms/users/create.html",
            generated_password=generated_password,
            username="",
            email="",
            full_name="",
            role="",
            tenants=tenants,
        )

    is_json = request.is_json
    raw = request.get_json() if is_json else request.form

    def _error(msg: str):
        if is_json:
            return jsonify({"error": msg}), 400
        flash(msg, "error")
        tenants = (
            Tenant.query.order_by(Tenant.name).all()
            if current_user.is_super_admin
            else []
        )
        return render_template(
            "cms/users/create.html",
            generated_password=raw.get("generated_password", ""),
            username=raw.get("username", ""),
            email=raw.get("email", ""),
            full_name=raw.get("full_name", ""),
            role=raw.get("role", ""),
            tenants=tenants,
        )

    try:
        validated = CreateUserSchema(**raw)
    except Exception:
        import traceback

        logger.warning("CreateUser validation error:\n%s", traceback.format_exc())
        return _error("Validation failed")

    data = validated.model_dump(exclude_none=True)

    # Check for duplicate username/email
    if User.query.filter_by(username=data["username"]).first():
        return _error("Username already exists")
    if User.query.filter_by(email=data["email"]).first():
        return _error("Email already exists")

    # Validate role
    valid_roles = [
        "admin",
        "senior_investigator",
        "investigator",
        "junior_investigator",
        "viewer",
    ]
    if data["role"] not in valid_roles:
        return _error("Invalid role")

    password = data.get("password") or data.get("generated_password")
    if not password:
        return _error("Password is required")

    if current_user.is_super_admin and data.get("tenant_id"):
        tenant_id = data["tenant_id"]
    else:
        tenant_id = current_user.tenant_id

    user = User(
        username=data["username"],
        email=data["email"],
        full_name=data["full_name"],
        role=data["role"],
        tenant_id=tenant_id,
    )
    user.set_password(password)

    db.session.add(user)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        new_values={"username": user.username, "role": user.role},
        description=f"Created user {user.username} with role {user.role}",
    )
    db.session.commit()

    notify_user_created(user.username, user.email, current_user.username)

    # Send credentials via email if requested
    if data.get("send_email") and user.email:
        from .email_utils import send_password_reset_email

        token = user.generate_reset_token()
        reset_url = url_for("auth.set_password", token=token, _external=True)
        sent = send_password_reset_email(
            user.email, user.username, user.full_name, reset_url
        )
        if sent:
            flash(f"Password reset link sent to {user.email}", "success")
        else:
            flash("Failed to send email — SMTP may not be configured.", "warning")

    if data.get("send_sms"):
        flash("SMS delivery not yet implemented.", "info")

    if request.is_json:
        result = user.to_dict()
        result["generated_password"] = password
        return jsonify({"message": "User created", "user": result}), 201

    flash(f"User {user.username} created successfully.", "success")
    return redirect(url_for("users.list_users"))


@users_bp.route("/<user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id: str) -> flask.Response:
    """Edit user details."""
    is_json = request.is_json
    # Users can edit their own profile, admins can edit anyone
    if user_id != current_user.id and not current_user.is_admin:
        if is_json:
            return jsonify({"error": "Access denied"}), 403
        flash("Access denied.", "danger")
        return redirect(url_for("users.list_users"))

    user = db.session.get(User, user_id) or abort(404)

    def _edit_render(**extra):
        tenants = (
            Tenant.query.order_by(Tenant.name).all()
            if current_user.is_super_admin
            else []
        )
        ctx = {"user": user, "tenants": tenants}
        ctx.update(extra)
        return render_template("cms/users/edit.html", **ctx)

    if request.method == "POST":
        raw = request.get_json() if is_json else request.form
        try:
            validated = EditUserSchema(**raw)
        except Exception:
            logger.exception("User edit validation failed")
            if is_json:
                return jsonify(
                    {"error": "Validation failed", "details": "Invalid data provided"}
                ), 400
            flash("Validation failed. Please check your input.", "danger")
            return _edit_render()
        data = validated.model_dump(exclude_none=True)
        old_values = {}
        changes = {}

        # Fields that can be edited by the user themselves
        user_editable = ["full_name", "email"]
        # Fields only editable by admins
        admin_editable = ["role", "is_active"]

        for field in user_editable:
            if field in data and getattr(user, field) != data[field]:
                old_values[field] = getattr(user, field)
                changes[field] = {"old": old_values[field], "new": data[field]}
                setattr(user, field, data[field])

        # Admin-only fields
        if current_user.is_admin:
            for field in admin_editable:
                if field in data and getattr(user, field) != data[field]:
                    old_values[field] = getattr(user, field)
                    value = data[field]
                    if field == "is_active" and isinstance(value, str):
                        value = value.lower() in ("1", "true", "yes", "on")
                    changes[field] = {"old": old_values[field], "new": value}
                    setattr(user, field, value)

            # Super admin can change tenant membership
            if (
                current_user.is_super_admin
                and data.get("tenant_id")
                and data["tenant_id"] != user.tenant_id
            ):
                new_tenant = db.session.get(Tenant, data["tenant_id"])
                if not new_tenant:
                    flash("Target tenant not found.", "danger")
                    return _edit_render()
                old_count = User.query.filter_by(tenant_id=user.tenant_id).count()
                if old_count <= 1:
                    flash(
                        "Cannot remove the last user from the current tenant.", "danger"
                    )
                    return _edit_render()
                old_values["tenant_id"] = user.tenant_id
                changes["tenant_id"] = {"old": user.tenant_id, "new": data["tenant_id"]}
                user.tenant_id = data["tenant_id"]

            # Password change (admin can set without old password)
            if data.get("password"):
                user.set_password(data["password"])
                changes["password"] = {"new": "***"}

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="user",
            entity_id=user.id,
            changes=changes if changes else None,
            ip_address=request.remote_addr,
            description=f"Updated user {user.username}",
        )
        db.session.commit()

        if request.is_json:
            return jsonify({"message": "User updated", "user": user.to_dict()})

        flash("User updated successfully.", "success")
        return redirect(url_for("users.view_user", user_id=user.id))

    return _edit_render()


@users_bp.route("/<user_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_user(user_id: str) -> flask.Response:
    """Deactivate a user account."""
    user = db.session.get(User, user_id) or abort(404)

    if user.id == current_user.id:
        return jsonify({"error": "Cannot deactivate your own account"}), 400

    user.is_active = False

    AuditLog.log(
        user_id=current_user.id,
        action="deactivate",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Deactivated user {user.username}",
    )
    db.session.commit()

    flash(f"User {user.username} has been deactivated.", "info")

    if request.is_json:
        return jsonify({"message": "User deactivated"})
    return redirect(url_for("users.list_users"))


@users_bp.route("/change-password", methods=["POST"])
@login_required
@rate_limit(limit=(10, 300), key_prefix="change_password")
@validate(ChangePasswordSchema)
def change_password() -> flask.Response:
    """Self-service password change. User must provide current password."""
    current_pw = request.validated_data.get("current_password", "")
    new_pw = request.validated_data.get("new_password", "")
    confirm_pw = request.validated_data.get("confirm_password", "")

    if not current_user.check_password(current_pw):
        return jsonify({"error": "Current password is incorrect"}), 400

    if new_pw != confirm_pw:
        return jsonify({"error": "New passwords do not match"}), 400

    current_user.set_password(new_pw)

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="user",
        entity_id=current_user.id,
        ip_address=request.remote_addr,
        description="User changed their own password",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"message": "Password changed successfully"})

    flash("Password changed successfully.", "success")
    return redirect(url_for("users.view_user", user_id=current_user.id))


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
    Return all case IDs the user can access.
    Replicates can_access_case() logic but in bulk.
    """
    from .models import Case, case_assignments

    # Admin bypass — returns all non-deleted case IDs
    if user.is_admin:
        return [
            r[0]
            for r in Case.query.with_entities(Case.id)
            .filter(Case.is_deleted == False)
            .all()
        ]

    # Build list of case IDs via all access patterns
    direct = (
        Case.query.with_entities(Case.id)
        .filter(
            Case.is_deleted == False,
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
