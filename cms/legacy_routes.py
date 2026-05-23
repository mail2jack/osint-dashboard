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
from datetime import datetime, date, timezone
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
from .routes.utils import (
    normalize_name, calculate_similarity,
    find_similar_subjects, find_similar_clients,
    check_for_exact_match, normalize_phone
)

logger = logging.getLogger(__name__)

cms_bp = Blueprint('cms', __name__, url_prefix='/cms')
