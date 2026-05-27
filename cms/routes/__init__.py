"""
Case Management System - Route Modules
=======================================
"""

import logging
import io
import csv
import json
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, date, timezone
from typing import Optional, Dict, Any

from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, flash, current_app, send_file, abort, Response
)
from flask_login import login_required, current_user

from ..models import (
    db, Case, Client, Subject, Finding, FinancialRecord,
    AuditLog, Document, User, CaseStatus, CasePriority,
    subject_relations, Comment,
    CommentEditHistory, DocumentTemplate, Reminder, ReminderType, ReminderRecurrence,
    Screenshot, Setting, SpiderFootScan, Address, Contact, init_default_settings,
    OsintSearch
)
from ..auth import (
    roles_required, admin_required, senior_required,
    can_export, case_access_required, case_edit_required
)
from ..encryption_utils import encryptor
from .utils import (
    normalize_name, calculate_similarity,
    find_similar_subjects, find_similar_clients,
    check_for_exact_match, normalize_phone
)

logger = logging.getLogger(__name__)

cms_bp = Blueprint('cms', __name__, url_prefix='/cms')


def register_modules() -> None:
    from . import dashboard
    from . import phone
    from . import email
    from . import kadaster
    from . import politiebureau
    from . import rdw
    from . import vessel
    from . import interpol
    from . import social_accounts
    from . import social_extraction
    from . import financials
    from . import findings
    from . import comments
    from . import settings
    from . import exports
    from . import documents
    from . import reminders
    from . import audit
    from . import system
    from . import search
    from . import templates
    from . import screenshots
    from . import clients_crud
    from . import clients_archive
    from . import cases_crud
    from . import cases_state
    from . import cases_subjects
    from . import cases_reports
    from . import osint_search
    from . import subjects_list
    from . import subjects_crud
    from . import subjects_faces
    from . import subjects_rel
    from . import spiderfoot
    from . import search_fts
    from . import help
