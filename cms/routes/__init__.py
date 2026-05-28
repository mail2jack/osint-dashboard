"""
Case Management System - Route Modules
=======================================
"""

import logging
from flask import Blueprint

logger = logging.getLogger(__name__)

cms_bp = Blueprint('cms', __name__, url_prefix='/cms')


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
