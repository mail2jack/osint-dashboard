"""
First-time Setup Wizard — guides the admin through post-install configuration.

Hooks:
  - _complete_2fa_login() redirects to /setup/welcome if wizard incomplete
  - cms_bp.before_request redirects non-XHR page loads to wizard
"""

import json
import logging

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..models import Setting, User, db

logger = logging.getLogger(__name__)

setup_wizard_bp = Blueprint("setup_wizard", __name__, url_prefix="/setup")

STEPS = ["welcome", "password", "api_keys", "smtp", "ai", "telegram", "finish"]
STEP_LABELS = {
    "welcome": "Welcome",
    "password": "Admin Password",
    "api_keys": "API Keys",
    "smtp": "Email (SMTP)",
    "ai": "AI Provider",
    "telegram": "Telegram Bot",
    "finish": "Done",
}
STEP_ICONS = {
    "welcome": "👋",
    "password": "🔑",
    "api_keys": "🔌",
    "smtp": "📧",
    "ai": "🤖",
    "telegram": "📱",
    "finish": "✅",
}


def _wizard_step():
    return Setting.get("setup_wizard_step", "welcome")


def _set_step(step):
    Setting.set("setup_wizard_step", step)


def _wizard_complete():
    return Setting.get("setup_wizard_complete") == "true"


def _mark_complete():
    Setting.set("setup_wizard_complete", "true")
    Setting.set("setup_wizard_step", "finish")


def _next_step(current_step):
    try:
        idx = STEPS.index(current_step)
        return STEPS[idx + 1] if idx + 1 < len(STEPS) else "finish"
    except (ValueError, IndexError):
        return "finish"


def _prev_step(current_step):
    try:
        idx = STEPS.index(current_step)
        return STEPS[idx - 1] if idx > 0 else None
    except (ValueError, IndexError):
        return None


def _skipped_steps():
    raw = Setting.get("setup_wizard_skipped", "[]")
    try:
        return set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return set()


def _mark_skipped(step):
    skipped = _skipped_steps()
    skipped.add(step)
    Setting.set("setup_wizard_skipped", json.dumps(list(skipped)))


def _configured_settings():
    """Return dict of settings that were configured during wizard."""
    result = {}
    for key in [
        "brave_api_key",
        "overheid_api_key",
        "hibp_api_key",
        "smtp_server",
        "smtp_from_email",
        "openrouter_api_key",
        "ollama_url",
        "telegram_bot_token",
        "telegram_enabled",
    ]:
        val = Setting.get(key)
        if val:
            result[key] = val
    return result


def is_wizard_required():
    """Check if the setup wizard should be shown to the current user."""
    if not current_user.is_authenticated:
        return False
    if not current_user.is_super_admin:
        return False
    if _wizard_complete():
        return False
    if request.path.startswith("/setup/"):
        return False
    if request.path.startswith("/auth/"):
        return False
    if request.path.startswith("/static/"):
        return False
    if request.path.startswith("/api/"):
        return False
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return False
    return True


@setup_wizard_bp.route("/")
@login_required
def wizard():
    step = _wizard_step()
    if _wizard_complete():
        return redirect(url_for("setup_wizard.finish"))
    return redirect(url_for(f"setup_wizard.{step}"))


@setup_wizard_bp.route("/welcome", methods=["GET", "POST"])
@login_required
def welcome():
    if _wizard_complete():
        return redirect(url_for("cms.dashboard"))
    if request.method == "POST":
        _set_step("password")
        return redirect(url_for("setup_wizard.password"))
    return render_template(
        "cms/setup_wizard.html",
        step="welcome",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=1,
        prev=None,
        next_step="password",
    )


@setup_wizard_bp.route("/password", methods=["GET", "POST"])
@login_required
def password():
    if _wizard_complete():
        return redirect(url_for("cms.dashboard"))
    user = User.query.get(current_user.id)
    is_default = user.check_password("changeme123")

    if request.method == "POST":
        if "skip" in request.form:
            _mark_skipped("password")
            _set_step("api_keys")
            flash(
                "Password change skipped. You can change it later via Profile.", "info"
            )
            return redirect(url_for("setup_wizard.api_keys"))

        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if is_default and current_pw != "changeme123":
            flash("Current password is incorrect (default is: changeme123).", "danger")
            return render_template(
                "cms/setup_wizard.html",
                step="password",
                steps=STEPS,
                labels=STEP_LABELS,
                icons=STEP_ICONS,
                total=len(STEPS),
                current=2,
                prev="welcome",
                next_step="api_keys",
                is_default=is_default,
            )

        if not is_default and current_pw and not user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
            return render_template(
                "cms/setup_wizard.html",
                step="password",
                steps=STEPS,
                labels=STEP_LABELS,
                icons=STEP_ICONS,
                total=len(STEPS),
                current=2,
                prev="welcome",
                next_step="api_keys",
                is_default=is_default,
            )

        if not new_pw:
            flash("New password is required.", "danger")
            return render_template(
                "cms/setup_wizard.html",
                step="password",
                steps=STEPS,
                labels=STEP_LABELS,
                icons=STEP_ICONS,
                total=len(STEPS),
                current=2,
                prev="welcome",
                next_step="api_keys",
                is_default=is_default,
            )

        if new_pw != confirm:
            flash("Passwords do not match.", "danger")
            return render_template(
                "cms/setup_wizard.html",
                step="password",
                steps=STEPS,
                labels=STEP_LABELS,
                icons=STEP_ICONS,
                total=len(STEPS),
                current=2,
                prev="welcome",
                next_step="api_keys",
                is_default=is_default,
            )

        if len(new_pw) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template(
                "cms/setup_wizard.html",
                step="password",
                steps=STEPS,
                labels=STEP_LABELS,
                icons=STEP_ICONS,
                total=len(STEPS),
                current=2,
                prev="welcome",
                next_step="api_keys",
                is_default=is_default,
            )

        user.set_password(new_pw)
        db.session.commit()
        flash("Password changed successfully.", "success")
        _set_step("api_keys")
        return redirect(url_for("setup_wizard.api_keys"))

    return render_template(
        "cms/setup_wizard.html",
        step="password",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=2,
        prev="welcome",
        next_step="api_keys",
        is_default=is_default,
    )


@setup_wizard_bp.route("/api_keys", methods=["GET", "POST"])
@login_required
def api_keys():
    if _wizard_complete():
        return redirect(url_for("cms.dashboard"))
    if request.method == "POST":
        if "skip" in request.form:
            _mark_skipped("api_keys")
            _set_step("smtp")
            flash(
                "API keys skipped. Configure them later via Settings > API Keys.",
                "info",
            )
            return redirect(url_for("setup_wizard.smtp"))

        brave = request.form.get("brave_api_key", "").strip()
        overheid = request.form.get("overheid_api_key", "").strip()
        hibp = request.form.get("hibp_api_key", "").strip()

        if brave:
            Setting.set(
                "brave_api_key",
                brave,
                category="api_keys",
                description="Brave Search API key",
            )
        if overheid:
            Setting.set(
                "overheid_api_key",
                overheid,
                category="api_keys",
                description="Overheid.io API key",
            )
        if hibp:
            Setting.set(
                "hibp_api_key",
                hibp,
                category="api_keys",
                description="Have I Been Pwned API key",
            )

        db.session.commit()
        if brave or overheid or hibp:
            flash("API keys saved.", "success")
        _set_step("smtp")
        return redirect(url_for("setup_wizard.smtp"))

    return render_template(
        "cms/setup_wizard.html",
        step="api_keys",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=3,
        prev="password",
        next_step="smtp",
    )


@setup_wizard_bp.route("/smtp", methods=["GET", "POST"])
@login_required
def smtp():
    if _wizard_complete():
        return redirect(url_for("cms.dashboard"))
    if request.method == "POST":
        if "skip" in request.form:
            _mark_skipped("smtp")
            _set_step("ai")
            flash("SMTP skipped. Configure it later via Settings > Email.", "info")
            return redirect(url_for("setup_wizard.ai"))

        server = request.form.get("smtp_server", "").strip()
        port = request.form.get("smtp_port", "587").strip()
        username = request.form.get("smtp_username", "").strip()
        password = request.form.get("smtp_password", "")
        from_email = request.form.get("smtp_from_email", "").strip()
        from_name = request.form.get("smtp_from_name", "Iveras CMS").strip()

        if server:
            Setting.set(
                "smtp_server",
                server,
                category="email",
                description="SMTP server hostname",
            )
            Setting.set(
                "smtp_port", port, category="email", description="SMTP server port"
            )
            if username:
                Setting.set(
                    "smtp_username",
                    username,
                    category="email",
                    description="SMTP username",
                )
            if password:
                Setting.set(
                    "smtp_password",
                    password,
                    category="email",
                    description="SMTP password",
                    encrypt=True,
                )
            if from_email:
                Setting.set(
                    "smtp_from_email",
                    from_email,
                    category="email",
                    description="Sender email address",
                )
            Setting.set(
                "smtp_from_name", from_name, category="email", description="Sender name"
            )
            db.session.commit()
            flash("SMTP settings saved.", "success")
        _set_step("ai")
        return redirect(url_for("setup_wizard.ai"))

    return render_template(
        "cms/setup_wizard.html",
        step="smtp",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=4,
        prev="api_keys",
        next_step="ai",
    )


@setup_wizard_bp.route("/ai", methods=["GET", "POST"])
@login_required
def ai():
    if _wizard_complete():
        return redirect(url_for("cms.dashboard"))
    if request.method == "POST":
        if "skip" in request.form:
            _mark_skipped("ai")
            _set_step("telegram")
            flash(
                "AI provider skipped. Configure it later via Settings > AI Provider.",
                "info",
            )
            return redirect(url_for("setup_wizard.telegram"))

        or_key = request.form.get("openrouter_api_key", "").strip()
        ollama_url = request.form.get("ollama_url", "").strip()
        ollama_model = request.form.get("ollama_model", "").strip()

        if or_key:
            Setting.set(
                "openrouter_api_key",
                or_key,
                category="ai",
                description="OpenRouter API key",
                encrypt=True,
            )
        if ollama_url:
            Setting.set(
                "ollama_url", ollama_url, category="ai", description="Ollama server URL"
            )
        if ollama_model:
            Setting.set(
                "ollama_model",
                ollama_model,
                category="ai",
                description="Ollama model name",
            )
        db.session.commit()
        if or_key or ollama_url:
            flash("AI provider settings saved.", "success")
        _set_step("telegram")
        return redirect(url_for("setup_wizard.telegram"))

    return render_template(
        "cms/setup_wizard.html",
        step="ai",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=5,
        prev="smtp",
        next_step="telegram",
    )


@setup_wizard_bp.route("/telegram", methods=["GET", "POST"])
@login_required
def telegram():
    if _wizard_complete():
        return redirect(url_for("cms.dashboard"))
    if request.method == "POST":
        if "skip" in request.form:
            _mark_skipped("telegram")
            _mark_complete()
            flash(
                "Telegram bot skipped. Configure it later via Settings > Telegram Bot.",
                "info",
            )
            return redirect(url_for("setup_wizard.finish"))

        token = request.form.get("telegram_bot_token", "").strip()
        enabled = "telegram_enabled" in request.form
        allowed = request.form.get("telegram_allowed_users", "").strip()

        if token:
            Setting.set(
                "telegram_bot_token",
                token,
                category="telegram",
                description="Telegram bot token",
                encrypt=True,
            )
        Setting.set(
            "telegram_enabled",
            "true" if enabled else "false",
            category="telegram",
            description="Telegram bot enabled",
        )
        if allowed:
            Setting.set(
                "telegram_allowed_users",
                allowed,
                category="telegram",
                description="Allowed Telegram usernames",
            )
        db.session.commit()
        if token:
            flash("Telegram bot settings saved.", "success")
        _mark_complete()
        return redirect(url_for("setup_wizard.finish"))

    return render_template(
        "cms/setup_wizard.html",
        step="telegram",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=6,
        prev="ai",
        next_step="finish",
    )


@setup_wizard_bp.route("/finish")
@login_required
def finish():
    if not _wizard_complete():
        _mark_complete()
    configured = _configured_settings()
    skipped = _skipped_steps()
    return render_template(
        "cms/setup_wizard.html",
        step="finish",
        steps=STEPS,
        labels=STEP_LABELS,
        icons=STEP_ICONS,
        total=len(STEPS),
        current=7,
        prev="telegram",
        next_step=None,
        configured=configured,
        skipped=skipped,
    )
