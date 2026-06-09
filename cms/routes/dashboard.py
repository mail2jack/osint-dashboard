import logging

from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..models import db, Case, Client, Subject, Finding, CaseStatus
from ..auth import apply_tenant_filter
from ..health_utils import check_external_services

logger = logging.getLogger(__name__)


@cms_bp.route("/")
@cms_bp.route("/dashboard")
@login_required
def dashboard() -> str:
    """Search-first dashboard with quick stats, case overview, and service health."""

    # Search redirect
    q = request.args.get("q", "").strip()
    if q:
        return redirect(url_for("cms.search", q=q))

    case_counts = dict(
        apply_tenant_filter(
            db.session.query(Case.status, db.func.count(Case.id)).filter(
                Case.is_deleted == False
            ),
            Case,
        )
        .group_by(Case.status)
        .all()
    )
    client_query = apply_tenant_filter(
        Client.query.filter_by(is_deleted=False, is_active=True), Client
    )
    subject_query = apply_tenant_filter(
        Subject.query.filter_by(is_deleted=False), Subject
    )
    finding_query = apply_tenant_filter(
        Finding.query.filter_by(is_deleted=False), Finding
    )

    stats = {
        "open_cases": case_counts.get(CaseStatus.OPEN.value, 0),
        "active_cases": case_counts.get(CaseStatus.ACTIVE.value, 0),
        "suspended_cases": case_counts.get(CaseStatus.SUSPENDED.value, 0),
        "closed_cases": case_counts.get(CaseStatus.CLOSED.value, 0),
        "total_clients": client_query.count(),
        "total_subjects": subject_query.count(),
        "total_findings": finding_query.count(),
    }

    from ..models import case_assignments

    assigned_ids = (
        db.session.query(case_assignments.c.case_id)
        .filter(case_assignments.c.user_id == current_user.id)
        .all()
    )
    assigned_ids = [row[0] for row in assigned_ids]

    my_cases = (
        Case.query.filter(
            Case.is_deleted == False,
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
            db.or_(
                Case.assigned_to == current_user.id,
                Case.lead_investigator_id == current_user.id,
                Case.id.in_(assigned_ids) if assigned_ids else Case.id == None,
            ),
        )
        .order_by(Case.updated_at.desc())
        .limit(10)
        .all()
    )

    health = check_external_services()

    return render_template(
        "cms/dashboard.html", stats=stats, my_cases=my_cases, health=health
    )


@cms_bp.route("/api/health-summary")
@csrf.exempt
@login_required
def health_summary():
    """Return service health status as JSON for the traffic light in the header."""
    health = check_external_services()
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
