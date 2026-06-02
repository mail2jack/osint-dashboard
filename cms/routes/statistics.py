import logging
from datetime import datetime, timedelta, timezone

from flask import render_template
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db,
    Case,
    Client,
    Subject,
    Finding,
    AuditLog,
    User,
    CaseStatus,
    CasePriority,
    Reminder,
    SpiderFootScan,
    Setting,
)
from ..auth import admin_required
from sqlalchemy import func

logger = logging.getLogger(__name__)


@cms_bp.route("/settings/statistics")
@login_required
@admin_required
def statistics() -> str:
    """Statistics page with all dashboard widgets."""
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
        "archived_cases": Case.query.filter_by(
            status=CaseStatus.ARCHIVED.value, is_deleted=False
        ).count(),
        "total_clients": Client.query.filter_by(
            is_deleted=False, is_active=True
        ).count(),
        "total_subjects": Subject.query.filter_by(is_deleted=False).count(),
        "total_findings": Finding.query.filter_by(is_deleted=False).count(),
        "high_risk_subjects": Subject.query.filter(
            Subject.risk_score >= 70, Subject.is_deleted == False
        ).count(),
    }

    status_labels = ["Open", "Active", "Suspended", "Closed", "Archived"]
    status_values = [
        stats["open_cases"],
        stats["active_cases"],
        stats["suspended_cases"],
        stats["closed_cases"],
        stats["archived_cases"],
    ]

    priority_data = {
        "critical": Case.query.filter_by(
            priority=CasePriority.CRITICAL.value, is_deleted=False
        ).count(),
        "high": Case.query.filter_by(
            priority=CasePriority.HIGH.value, is_deleted=False
        ).count(),
        "medium": Case.query.filter_by(
            priority=CasePriority.MEDIUM.value, is_deleted=False
        ).count(),
        "low": Case.query.filter_by(
            priority=CasePriority.LOW.value, is_deleted=False
        ).count(),
    }

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_cases = Case.query.filter(
        Case.created_at >= thirty_days_ago, Case.is_deleted == False
    ).count()

    subject_types = (
        db.session.query(Subject.subject_type, func.count(Subject.id))
        .filter(Subject.is_deleted == False)
        .group_by(Subject.subject_type)
        .all()
    )
    subject_type_labels = [s[0] for s in subject_types]
    subject_type_values = [s[1] for s in subject_types]

    _instr = func.instr if db.engine.dialect.name == "sqlite" else func.strpos
    case_type_stats = (
        db.session.query(
            func.substr(Case.case_type, 1, _instr(Case.case_type, "|") - 1).label(
                "code"
            ),
            func.count(Case.id).label("count"),
        )
        .filter(
            Case.is_deleted == False,
            Case.case_type.isnot(None),
            Case.case_type != "",
            Case.case_type.like("%|%"),
        )
        .group_by(func.substr(Case.case_type, 1, _instr(Case.case_type, "|") - 1))
        .order_by(func.count(Case.id).desc())
        .limit(10)
        .all()
    )

    case_type_labels = [s.code if s.code else "Unknown" for s in case_type_stats]
    case_type_values = [s.count for s in case_type_stats]

    lead_investigator_stats = (
        db.session.query(User.full_name, func.count(Case.id).label("case_count"))
        .join(Case, Case.lead_investigator_id == User.id)
        .filter(
            Case.is_deleted == False,
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
        )
        .group_by(User.id, User.full_name)
        .order_by(func.count(Case.id).desc())
        .all()
    )

    investigator_names = [s.full_name for s in lead_investigator_stats]
    investigator_counts = [s.case_count for s in lead_investigator_stats]

    from ..models import case_assignments

    my_assigned_ids = (
        db.session.query(case_assignments.c.case_id)
        .filter(case_assignments.c.user_id == current_user.id)
        .all()
    )
    my_assigned_ids = [row[0] for row in my_assigned_ids]

    my_open_cases = Case.query.filter(
        Case.is_deleted == False,
        Case.status == CaseStatus.OPEN.value,
        db.or_(
            Case.assigned_to == current_user.id,
            Case.lead_investigator_id == current_user.id,
            Case.id.in_(my_assigned_ids) if my_assigned_ids else False,
        ),
    ).count()

    my_active_cases = Case.query.filter(
        Case.is_deleted == False,
        Case.status == CaseStatus.ACTIVE.value,
        db.or_(
            Case.assigned_to == current_user.id,
            Case.lead_investigator_id == current_user.id,
            Case.id.in_(my_assigned_ids) if my_assigned_ids else False,
        ),
    ).count()

    overdue_cases = Case.query.filter(
        Case.is_deleted == False,
        Case.target_end_date < datetime.now(timezone.utc).date(),
        Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
    ).count()

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

    recent_activity = (
        AuditLog.query.options(db.joinedload(AuditLog.user))
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
        .all()
    )

    priority_cases = (
        Case.query.filter(
            Case.priority.in_([CasePriority.CRITICAL.value, CasePriority.HIGH.value]),
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
            Case.is_deleted == False,
        )
        .order_by(Case.start_date.asc())
        .limit(5)
        .all()
    )

    now = datetime.now(timezone.utc)
    overdue_reminders = (
        Reminder.query.filter(
            Reminder.is_deleted == False,
            Reminder.is_completed == False,
            Reminder.reminder_date < now,
        )
        .order_by(Reminder.reminder_date.asc())
        .limit(10)
        .all()
    )

    upcoming_reminders = (
        Reminder.query.filter(
            Reminder.is_deleted == False,
            Reminder.is_completed == False,
            Reminder.reminder_date >= now,
        )
        .order_by(Reminder.reminder_date.asc())
        .limit(5)
        .all()
    )

    for r in overdue_reminders:
        r.is_overdue = True
    if overdue_reminders:
        db.session.commit()

    # SpiderFoot stats
    sf_total = SpiderFootScan.query.filter_by(is_deleted=False).count()
    sf_running = SpiderFootScan.query.filter_by(
        is_deleted=False, status="RUNNING"
    ).count()
    sf_completed = SpiderFootScan.query.filter_by(
        is_deleted=False, status="FINISHED"
    ).count()
    sf_failed = SpiderFootScan.query.filter_by(
        is_deleted=False, status="FAILED"
    ).count()
    sf_last_scan = (
        SpiderFootScan.query.filter_by(is_deleted=False)
        .order_by(SpiderFootScan.created_at.desc())
        .first()
    )
    sf_last_scan_time = sf_last_scan.created_at.isoformat() if sf_last_scan else None
    sf_health = Setting.get("spiderfoot_health", "")
    sf_last_ok = Setting.get("spiderfoot_last_ok", "")

    return render_template(
        "cms/settings/statistics.html",
        stats=stats,
        my_cases=my_cases,
        recent_activity=recent_activity,
        priority_cases=priority_cases,
        status_labels=status_labels,
        status_values=status_values,
        priority_data=priority_data,
        recent_cases=recent_cases,
        subject_type_labels=subject_type_labels,
        subject_type_values=subject_type_values,
        overdue_reminders=overdue_reminders,
        upcoming_reminders=upcoming_reminders,
        case_type_labels=case_type_labels,
        case_type_values=case_type_values,
        investigator_names=investigator_names,
        investigator_counts=investigator_counts,
        my_open_cases=my_open_cases,
        my_active_cases=my_active_cases,
        overdue_cases=overdue_cases,
        sf_total=sf_total,
        sf_running=sf_running,
        sf_completed=sf_completed,
        sf_failed=sf_failed,
        sf_last_scan_time=sf_last_scan_time,
        sf_health=sf_health,
        sf_last_ok=sf_last_ok,
    )
