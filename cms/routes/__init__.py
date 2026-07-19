"""
Case Management System - Route Modules
=======================================
"""

import logging
from flask import Blueprint, request

logger = logging.getLogger(__name__)

cms_bp = Blueprint("cms", __name__, url_prefix="/cms")


@cms_bp.before_request
def _set_identity_from_case() -> None:
    """Automatically set Tor identity isolation from case_id URL parameter."""
    try:
        from cms.services.identity_isolation import (
            is_identity_isolation_enabled,
            set_identity_for_case,
            reset_identity,
        )

        if not is_identity_isolation_enabled():
            return

        case_id = request.view_args.get("case_id") if request.view_args else None
        if case_id:
            set_identity_for_case(case_id)
        else:
            reset_identity()
    except Exception:
        pass


@cms_bp.before_request
def _redirect_to_setup_wizard() -> None:
    """Redirect superadmins to the setup wizard if they haven't completed it."""
    try:
        from flask import current_app

        if current_app.testing:
            return

        from cms.routes.setup_wizard import is_wizard_required

        if is_wizard_required():
            from flask import redirect, url_for

            return redirect(url_for("setup_wizard.wizard"))
    except Exception:
        pass


def register_modules() -> None:
    from . import dashboard  # noqa: F401
    from . import phone  # noqa: F401
    from . import email  # noqa: F401
    from . import kadaster  # noqa: F401
    from . import politiebureau  # noqa: F401
    from . import rdw  # noqa: F401
    from . import vessel  # noqa: F401
    from . import interpol  # noqa: F401
    from . import social_accounts  # noqa: F401
    from . import social_extraction  # noqa: F401
    from . import financials  # noqa: F401
    from . import findings  # noqa: F401
    from . import comments  # noqa: F401
    from . import settings  # noqa: F401
    from . import analytics  # noqa: F401
    from . import exports  # noqa: F401
    from . import documents  # noqa: F401
    from . import reminders  # noqa: F401
    from . import audit  # noqa: F401
    from . import system  # noqa: F401
    from . import search  # noqa: F401
    from . import templates  # noqa: F401
    from . import screenshots  # noqa: F401
    from . import clients_crud  # noqa: F401
    from . import clients_archive  # noqa: F401
    from . import cases_crud  # noqa: F401
    from . import cases_state  # noqa: F401
    from . import cases_subjects  # noqa: F401
    from . import cases_reports  # noqa: F401
    from . import osint_search  # noqa: F401
    from . import subjects_list  # noqa: F401
    from . import subjects_crud  # noqa: F401
    from . import subjects_faces  # noqa: F401
    from . import subjects_rel  # noqa: F401
    from . import spiderfoot  # noqa: F401
    from . import search_fts  # noqa: F401
    from . import help  # noqa: F401
    from . import api_keys  # noqa: F401
    from . import imports  # noqa: F401
    from . import kvk  # noqa: F401
    from . import notifications_api  # noqa: F401
    from . import notifications  # noqa: F401
    from . import translations  # noqa: F401
    from . import statistics  # noqa: F401
    from . import demo  # noqa: F401
    from . import invoicing  # noqa: F401
    from . import credit_notes  # noqa: F401
    from . import stripe_billing  # noqa: F401
    from . import feature_flags_admin  # noqa: F401
    from . import rate_limits_admin  # noqa: F401
    from . import gdpr  # noqa: F401
    from . import dpa  # noqa: F401
    from . import breach  # noqa: F401
    from . import opsec_dashboard  # noqa: F401

    # Background task status API
    from ..background import register_background_routes

    register_background_routes(cms_bp)
