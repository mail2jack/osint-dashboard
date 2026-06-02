import logging

from flask import render_template, request, redirect, url_for
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Case, Client, Subject, Finding, CaseStatus
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

    stats = {
        "open_cases": Case.query.filter_by(
            status=CaseStatus.OPEN.value, is_deleted=False
        ).count(),
        "active_cases": Case.query.filter_by(
            status=CaseStatus.ACTIVE.value, is_deleted=False
        ).count(),
        "suspended_cases": Case.query.filter_by(
            status=CaseStatus.SUSPENDED.value, is_deleted=False
        ).count(),
        "closed_cases": Case.query.filter_by(
            status=CaseStatus.CLOSED.value, is_deleted=False
        ).count(),
        "total_clients": Client.query.filter_by(
            is_deleted=False, is_active=True
        ).count(),
        "total_subjects": Subject.query.filter_by(is_deleted=False).count(),
        "total_findings": Finding.query.filter_by(is_deleted=False).count(),
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
