"""
Authentication & User Management Routes
========================================
"""

import hashlib
import io
import json
import base64
import re
import secrets
import uuid as _uuid
import logging
from datetime import datetime, timezone

import flask
import pyotp
import qrcode

from flask import (
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    session,
    abort,
)
from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user,
)

from ..auth import (
    auth_bp,
    users_bp,
    MAX_LOGIN_ATTEMPTS,
    ACCOUNT_LOCKOUT_MINUTES,
    admin_required,
    roles_required,
)
from ..models import db, User, AuditLog, Case, Tenant
from ..validation import validate
from ..rate_limiting import is_rate_limited, rate_limit, rate_limit_after_n
from ..notifications import (
    notify_login_failed,
    notify_login_success,
    notify_account_locked,
    notify_signup,
    notify_user_created,
)
from ..validation import (
    LoginSchema,
    SignupSchema,
    SetPasswordSchema,
    CreateUserSchema,
    EditUserSchema,
    ChangePasswordSchema,
)
from ..geo_utils import log_login_attempt

logger = logging.getLogger(__name__)


# =============================================================================
# Authentication Routes
# =============================================================================


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "organization"


@auth_bp.route("/signup", methods=["GET", "POST"])
@rate_limit(limit=(5, 300), key_prefix="signup_ip")
def signup() -> flask.Response:
    """Self-service signup with auto-provisioned tenant."""
    if current_user.is_authenticated:
        return redirect(url_for("cms.dashboard"))

    if request.method == "POST":
        d = request.form.to_dict() if request.form else {}
        honeypot = d.pop("_hp", "")
        if honeypot:
            flash("Signup rejected.", "danger")
            return render_template("cms/signup.html")

        try:
            validated = SignupSchema(**d)
        except Exception as e:
            if hasattr(e, "errors"):
                msgs = [
                    f"{' → '.join(str(seg) for seg in err.get('loc', []))}: {err.get('msg', '?')}"
                    for err in e.errors()
                ]
                flash(" | ".join(msgs), "danger")
            else:
                flash("Validation failed. Please check your input.", "danger")
            return render_template(
                "cms/signup.html",
                full_name=d.get("full_name", ""),
                email=d.get("email", ""),
                organization_name=d.get("organization_name", ""),
            )

        email = validated.email.strip().lower()
        if User.query.filter(db.func.lower(User.email) == email).first():
            flash("An account with this email already exists.", "danger")
            return render_template(
                "cms/signup.html",
                full_name=validated.full_name,
                organization_name=validated.organization_name,
            )

        if validated.password != validated.confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template(
                "cms/signup.html",
                full_name=validated.full_name,
                email=email,
                organization_name=validated.organization_name,
            )

        org_name = validated.organization_name.strip()
        slug = _slugify(org_name)
        existing = Tenant.query.filter_by(slug=slug).first()
        if existing:
            counter = 1
            while Tenant.query.filter_by(slug=f"{slug}-{counter}").first():
                counter += 1
            slug = f"{slug}-{counter}"

        tenant = Tenant(
            id=str(_uuid.uuid4()),
            name=org_name,
            slug=slug,
            is_active=True,
            tier="free",
        )
        db.session.add(tenant)

        # Use email as username, or derive from email prefix
        username = email.split("@")[0]
        if User.query.filter_by(username=username).first():
            username = f"{username}_{secrets.token_hex(2)}"

        user = User(
            username=username,
            email=email,
            full_name=validated.full_name.strip(),
            role="admin",
            tenant_id=tenant.id,
            is_super_admin=False,
            is_active=True,
        )
        user.set_password(validated.password)
        db.session.add(user)
        db.session.flush()

        tenant.owner_id = user.id
        db.session.commit()

        notify_signup(username=username, email=email, org_name=org_name)

        AuditLog.log(
            user_id=user.id,
            action="signup",
            entity_type="user",
            entity_id=user.id,
            ip_address=request.remote_addr,
            description=f"User {username} signed up and created organization {org_name}",
        )
        db.session.commit()

        # Auto-login and redirect to 2FA setup (same flow as password verification)
        session["_2fa_user_id"] = user.id
        session["_2fa_remember"] = False
        flash("Account created! Please set up two-factor authentication.", "info")
        return redirect(url_for("auth.setup_2fa"))

    return render_template("cms/signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login() -> flask.Response:
    """User login page."""
    if current_user.is_authenticated:
        return redirect(url_for("cms.dashboard"))

    if request.method == "POST":
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

            notify_login_success(username, request.remote_addr)

            session["_2fa_user_id"] = user.id
            session["_2fa_remember"] = remember
            if user.totp_secret:
                return redirect(url_for("auth.verify_2fa"))
            return redirect(url_for("auth.setup_2fa"))

        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_LOGIN_ATTEMPTS:
                from datetime import timedelta

                user.locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=ACCOUNT_LOCKOUT_MINUTES
                )
                notify_account_locked(
                    username, request.remote_addr, ACCOUNT_LOCKOUT_MINUTES
                )
        db.session.commit()

        notify_login_failed(username, request.remote_addr)

        if user:
            log_login_attempt(
                user_id=user.id,
                ip_address=request.remote_addr or "",
                is_success=False,
                user_agent=request.user_agent.string or "",
            )

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

        totp = pyotp.TOTP(user.totp_secret)
        if code and totp.verify(code, valid_window=1):
            return _complete_2fa_login(user)

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

        rate_limit_after_n(rate_key, max_attempts=3, retry_after=15)

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

        backup_codes = _generate_backup_codes()

        user.totp_secret = secret
        user.totp_enabled = True
        user.backup_codes = json.dumps(
            [hashlib.sha256(c.encode()).hexdigest() for c in backup_codes]
        )
        db.session.commit()

        session.pop("_2fa_pending_secret", None)

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

    secret = pyotp.random_base32()
    session["_2fa_pending_secret"] = secret
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(user.username, issuer_name="CMS")

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


@users_bp.route("/")
@login_required
@roles_required("admin", "senior_investigator")
def list_users() -> str:
    """List all users with pagination and optional tenant filter."""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    search = request.args.get("search", "")
    tenant_filter = request.args.get("tenant_id", "")

    query = User.query.filter_by(is_active=True)

    if not current_user.is_super_admin:
        query = query.filter(User.tenant_id == current_user.tenant_id)
    elif tenant_filter:
        query = query.filter(User.tenant_id == tenant_filter)

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

    tenants = (
        Tenant.query.order_by(Tenant.name).all() if current_user.is_super_admin else []
    )

    return render_template(
        "cms/users/list.html",
        users=pagination.items,
        pagination=pagination,
        search=search,
        tenants=tenants,
        selected_tenant_id=tenant_filter,
    )


@users_bp.route("/<user_id>")
@login_required
def view_user(user_id: str) -> str:
    """View user details."""
    if user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Access denied"}), 403

    user = db.session.get(User, user_id) or abort(404)
    return render_template("cms/users/view.html", user=user)


@users_bp.route("/<user_id>/activity")
@login_required
def user_activity(user_id: str) -> str:
    """View activity timeline for a specific user."""
    if user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Access denied"}), 403

    user = db.session.get(User, user_id) or abort(404)

    page = request.args.get("page", 1, type=int)
    per_page = 50
    entity_type = request.args.get("entity_type", "")
    action = request.args.get("action", "")

    query = AuditLog.query.filter_by(user_id=user_id)

    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    if action:
        query = query.filter_by(action=action)

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

    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

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

    total_actions = AuditLog.query.filter_by(user_id=user_id).count()
    today = datetime.now(timezone.utc).date()
    today_actions = AuditLog.query.filter(
        AuditLog.user_id == user_id, db.func.date(AuditLog.timestamp) == today
    ).count()

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


def _generate_password(length: int = 16) -> str:
    """Generate a random password guaranteed to pass complexity checks."""
    import random as _random
    import string as _string

    guaranteed = [
        secrets.choice(_string.ascii_uppercase),
        secrets.choice(_string.ascii_lowercase),
        secrets.choice(_string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    alphabet = _string.ascii_letters + _string.digits + "!@#$%^&*"
    remaining = [secrets.choice(alphabet) for _ in range(length - 4)]
    all_chars = guaranteed + remaining
    _random.SystemRandom().shuffle(all_chars)
    return "".join(all_chars)


@users_bp.route("/api/generate-password")
@login_required
@admin_required
def generate_password_api() -> flask.Response:
    """Generate a random password for the create user form."""
    return jsonify({"password": _generate_password()})


@users_bp.route("/create", methods=["GET", "POST"])
@login_required
@admin_required
@rate_limit(limit=(30, 300), key_prefix="create_user")
def create_user() -> flask.Response:
    """Create a new user (admin only). Password is auto-generated by default."""
    generated_password = None

    if request.method == "GET":
        generated_password = _generate_password()
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
    except Exception as e:
        import traceback

        logger.warning("CreateUser validation error:\n%s", traceback.format_exc())
        if hasattr(e, "errors"):
            msgs = [
                f"{' → '.join(str(seg) for seg in err.get('loc', []))}: {err.get('msg', '?')}"
                for err in e.errors()
            ]
            return _error(" | ".join(msgs))
        return _error("Validation failed")

    data = validated.model_dump(exclude_none=True)

    if User.query.filter_by(username=data["username"]).first():
        return _error("Username already exists")
    if User.query.filter_by(email=data["email"]).first():
        return _error("Email already exists")

    valid_roles = [
        "admin",
        "senior_investigator",
        "investigator",
        "junior_investigator",
        "viewer",
    ]
    if data["role"] not in valid_roles:
        return _error("Invalid role")

    user = User(
        username=data["username"],
        email=data.get("email", ""),
        full_name=data.get("full_name", ""),
        role=data["role"],
        tenant_id=data.get("tenant_id") or current_user.tenant_id,
        is_active=True,
    )

    password = data.get("generated_password")
    if password:
        user.set_password(password)
    else:
        password = _generate_password()
        user.set_password(password)

    db.session.add(user)
    db.session.commit()

    notify_user_created(
        username=user.username,
        email=user.email,
        created_by=current_user.username,
    )

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="user",
        entity_id=user.id,
        ip_address=request.remote_addr,
        description=f"Created user {user.username} with role {user.role}",
    )
    db.session.commit()

    if data.get("send_email", False):
        try:
            from ..email_utils import send_password_reset_email

            send_password_reset_email(
                user.email,
                user.username,
                user.full_name,
                url_for("auth.login", _external=True),
            )
        except Exception as e:
            logger.error("Failed to send password reset email to %s: %s", user.email, e)

    if is_json:
        return jsonify(
            {
                "message": "User created successfully",
                "password": password,
                "user": {"id": user.id, "username": user.username},
            }
        )

    flash(
        f"User {user.username} created successfully. Password: {password}",
        "success",
    )
    return redirect(url_for("users.list_users"))


@users_bp.route("/<user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id) -> flask.Response:
    """Edit user details. Admins can edit any user; regular users can edit their own profile."""
    user = db.session.get(User, user_id) or abort(404)

    if user_id != current_user.id and not current_user.is_admin:
        if request.is_json:
            return jsonify({"error": "Access denied"}), 403
        flash("You do not have permission to edit this user.", "danger")
        return redirect(url_for("users.list_users"))

    if request.method == "POST":
        is_json = request.is_json
        raw = request.get_json() if is_json else request.form
        try:
            validated = EditUserSchema(**raw)
        except Exception:
            if is_json:
                return jsonify({"error": "Invalid input"}), 400
            flash("Invalid input. Please check your data.", "danger")
            return render_template("cms/users/edit.html", user=user)

        data = validated.model_dump(exclude_none=True)

        if "username" in data and data["username"] != user.username:
            if User.query.filter_by(username=data["username"]).first():
                if is_json:
                    return jsonify({"error": "Username already exists"}), 400
                flash("Username already exists.", "danger")
                return render_template("cms/users/edit.html", user=user)

        if "email" in data and data["email"] != user.email:
            if User.query.filter_by(email=data["email"]).first():
                if is_json:
                    return jsonify({"error": "Email already exists"}), 400
                flash("Email already exists.", "danger")
                return render_template("cms/users/edit.html", user=user)

        if current_user.is_admin:
            for field in ["username", "email", "full_name", "role", "is_active"]:
                if field in data:
                    setattr(user, field, data[field])
        else:
            for field in ["full_name"]:
                if field in data:
                    setattr(user, field, data[field])

        if "password" in data and data["password"]:
            user.set_password(data["password"])

        db.session.commit()

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="user",
            entity_id=user.id,
            ip_address=request.remote_addr,
            description=f"Updated user {user.username}",
        )
        db.session.commit()

        if is_json:
            return jsonify({"message": "User updated"})

        flash("User updated successfully.", "success")
        return redirect(url_for("users.view_user", user_id=user.id))

    return render_template("cms/users/edit.html", user=user)


@users_bp.route("/<user_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_user(user_id) -> flask.Response:
    """Deactivate a user account (admin only)."""
    user = db.session.get(User, user_id) or abort(404)

    if user.id == current_user.id:
        if request.is_json:
            return jsonify({"error": "Cannot deactivate yourself"}), 400
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("users.list_users"))

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
