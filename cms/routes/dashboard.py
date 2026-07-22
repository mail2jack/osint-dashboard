import logging
import threading
import time

from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Case, Client, Subject, Finding, CaseStatus
from ..auth import apply_tenant_filter
from ..health_utils import check_external_services

logger = logging.getLogger(__name__)

_health_cache: dict[str, tuple[float, dict]] = {}
_health_cache_lock = threading.Lock()
_HEALTH_CACHE_TTL = 300


def _get_cached_health() -> dict:
    now = time.time()
    with _health_cache_lock:
        cached = _health_cache.get("health")
        if cached and (now - cached[0]) < _HEALTH_CACHE_TTL:
            return cached[1]
    fresh = check_external_services()
    with _health_cache_lock:
        _health_cache["health"] = (time.time(), fresh)
    return fresh


@cms_bp.route("/")
@cms_bp.route("/dashboard")
@login_required
def dashboard() -> str:
    """Search-first dashboard with quick stats and case overview."""

    q = request.args.get("q", "").strip()
    if q:
        return redirect(url_for("cms.search", q=q))

    case_counts = dict(
        apply_tenant_filter(
            db.session.query(Case.status, db.func.count(Case.id)).filter(
                Case.is_deleted == False,
                Case.archived_at.is_(None),
            ),
            Case,
        )
        .group_by(Case.status)
        .all()
    )
    stats = {
        "open_cases": case_counts.get(CaseStatus.OPEN.value, 0),
        "active_cases": case_counts.get(CaseStatus.ACTIVE.value, 0),
        "suspended_cases": case_counts.get(CaseStatus.SUSPENDED.value, 0),
        "closed_cases": case_counts.get(CaseStatus.CLOSED.value, 0),
        "total_clients": apply_tenant_filter(
            db.session.query(db.func.count(Client.id)).filter(
                Client.is_deleted == False, Client.is_active == True
            ),
            Client,
        ).scalar(),
        "total_subjects": apply_tenant_filter(
            db.session.query(db.func.count(Subject.id)).filter(
                Subject.is_deleted == False
            ),
            Subject,
        ).scalar(),
        "total_findings": apply_tenant_filter(
            db.session.query(db.func.count(Finding.id)).filter(
                Finding.is_deleted == False
            ),
            Finding,
        ).scalar(),
    }

    from ..models import case_assignments

    assigned_ids = (
        db.session.query(case_assignments.c.case_id)
        .filter(case_assignments.c.user_id == current_user.id)
        .all()
    )
    assigned_ids = [row[0] for row in assigned_ids]

    my_cases_q = Case.query.filter(
        Case.is_deleted == False,
        Case.archived_at.is_(None),
        Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
        db.or_(
            Case.assigned_to == current_user.id,
            Case.lead_investigator_id == current_user.id,
            Case.id.in_(assigned_ids) if assigned_ids else Case.id == None,
        ),
    )
    my_cases_q = apply_tenant_filter(my_cases_q, Case)
    my_cases = my_cases_q.order_by(Case.updated_at.desc()).limit(10).all()

    return render_template("cms/dashboard.html", stats=stats, my_cases=my_cases)


@cms_bp.route("/api/health-summary")
@login_required
def health_summary():
    """Return service health status as JSON for the traffic light in the header."""
    health = _get_cached_health()
    green = []
    orange = []
    red = []
    for name, status in health.items():
        if status == "ok" or status == "disabled" or status == "connected":
            green.append(name)
        elif status == "no key configured" or status == "auth error":
            orange.append(name)
        elif status.startswith("no_"):
            green.append(name)
        else:
            red.append(name)

    if len(red) == 0 and len(orange) == 0:
        state = "green"
    elif len(red) == len(health):
        state = "red"
    else:
        state = "orange"

    return jsonify({"state": state, "services": health})
