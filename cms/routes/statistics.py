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
from ..auth import admin_required, apply_tenant_filter
from sqlalchemy import func

logger = logging.getLogger(__name__)


def _compute_case_stats() -> dict:
    """Case statistics: counts by status/priority, totals, recent, overdue."""
    case_counts = dict(
        apply_tenant_filter(
            db.session.query(Case.status, func.count(Case.id))
            .filter(Case.is_deleted == False)
            .group_by(Case.status),
            Case,
        ).all()
    )
    priority_counts = dict(
        apply_tenant_filter(
            db.session.query(Case.priority, func.count(Case.id))
            .filter(Case.is_deleted == False)
            .group_by(Case.priority),
            Case,
        ).all()
    )

    stats = {
        "open_cases": case_counts.get(CaseStatus.OPEN.value, 0),
        "active_cases": case_counts.get(CaseStatus.ACTIVE.value, 0),
        "suspended_cases": case_counts.get(CaseStatus.SUSPENDED.value, 0),
        "closed_cases": case_counts.get(CaseStatus.CLOSED.value, 0),
        "archived_cases": case_counts.get(CaseStatus.ARCHIVED.value, 0),
        "total_clients": apply_tenant_filter(
            Client.query.filter_by(is_deleted=False, is_active=True), Client
        ).count(),
        "total_subjects": apply_tenant_filter(
            Subject.query.filter_by(is_deleted=False), Subject
        ).count(),
        "total_findings": apply_tenant_filter(
            Finding.query.filter_by(is_deleted=False), Finding
        ).count(),
        "high_risk_subjects": apply_tenant_filter(
            Subject.query.filter(Subject.risk_score >= 70, Subject.is_deleted == False),
            Subject,
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
        "critical": priority_counts.get(CasePriority.CRITICAL.value, 0),
        "high": priority_counts.get(CasePriority.HIGH.value, 0),
        "medium": priority_counts.get(CasePriority.MEDIUM.value, 0),
        "low": priority_counts.get(CasePriority.LOW.value, 0),
    }

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_cases = apply_tenant_filter(
        Case.query.filter(Case.created_at >= thirty_days_ago, Case.is_deleted == False),
        Case,
    ).count()

    overdue_cases = apply_tenant_filter(
        Case.query.filter(
            Case.is_deleted == False,
            Case.target_end_date < datetime.now(timezone.utc).date(),
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
        ),
        Case,
    ).count()

    return {
        "stats": stats,
        "status_labels": status_labels,
        "status_values": status_values,
        "priority_data": priority_data,
        "recent_cases": recent_cases,
        "overdue_cases": overdue_cases,
    }


def _compute_subject_stats() -> dict:
    """Subject statistics: type breakdown, case types, investigator workload."""
    subject_types = apply_tenant_filter(
        db.session.query(Subject.subject_type, func.count(Subject.id))
        .filter(Subject.is_deleted == False)
        .group_by(Subject.subject_type),
        Subject,
    ).all()
    subject_type_labels = [s[0] for s in subject_types]
    subject_type_values = [s[1] for s in subject_types]

    _instr = func.instr if db.engine.dialect.name == "sqlite" else func.strpos
    case_type_stats = apply_tenant_filter(
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
        .limit(10),
        Case,
    ).all()

    case_type_labels = [s.code if s.code else "Unknown" for s in case_type_stats]
    case_type_values = [s.count for s in case_type_stats]

    lead_investigator_stats = apply_tenant_filter(
        db.session.query(User.full_name, func.count(Case.id).label("case_count"))
        .join(Case, Case.lead_investigator_id == User.id)
        .filter(
            Case.is_deleted == False,
            Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
        )
        .group_by(User.id, User.full_name)
        .order_by(func.count(Case.id).desc()),
        Case,
    ).all()

    investigator_names = [s.full_name for s in lead_investigator_stats]
    investigator_counts = [s.case_count for s in lead_investigator_stats]

    return {
        "subject_type_labels": subject_type_labels,
        "subject_type_values": subject_type_values,
        "case_type_labels": case_type_labels,
        "case_type_values": case_type_values,
        "investigator_names": investigator_names,
        "investigator_counts": investigator_counts,
    }


def _compute_activity_stats() -> dict:
    """Activity timeline: my cases, recent activity, priority cases."""
    from ..models import case_assignments

    my_assigned_ids = [
        row[0]
        for row in db.session.query(case_assignments.c.case_id)
        .filter(case_assignments.c.user_id == current_user.id)
        .all()
    ]

    my_open_cases = apply_tenant_filter(
        Case.query.filter(
            Case.is_deleted == False,
            Case.status == CaseStatus.OPEN.value,
            db.or_(
                Case.assigned_to == current_user.id,
                Case.lead_investigator_id == current_user.id,
                Case.id.in_(my_assigned_ids) if my_assigned_ids else False,
            ),
        ),
        Case,
    ).count()

    my_active_cases = apply_tenant_filter(
        Case.query.filter(
            Case.is_deleted == False,
            Case.status == CaseStatus.ACTIVE.value,
            db.or_(
                Case.assigned_to == current_user.id,
                Case.lead_investigator_id == current_user.id,
                Case.id.in_(my_assigned_ids) if my_assigned_ids else False,
            ),
        ),
        Case,
    ).count()

    my_cases = (
        apply_tenant_filter(
            Case.query.filter(
                Case.is_deleted == False,
                Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
                db.or_(
                    Case.assigned_to == current_user.id,
                    Case.lead_investigator_id == current_user.id,
                    Case.id.in_(my_assigned_ids)
                    if my_assigned_ids
                    else Case.id == None,
                ),
            ),
            Case,
        )
        .order_by(Case.updated_at.desc())
        .limit(10)
        .all()
    )

    recent_activity = (
        apply_tenant_filter(
            AuditLog.query.options(db.joinedload(AuditLog.user)),
            AuditLog,
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
        .all()
    )

    priority_cases = (
        apply_tenant_filter(
            Case.query.filter(
                Case.priority.in_(
                    [CasePriority.CRITICAL.value, CasePriority.HIGH.value]
                ),
                Case.status.in_([CaseStatus.OPEN.value, CaseStatus.ACTIVE.value]),
                Case.is_deleted == False,
            ),
            Case,
        )
        .order_by(Case.start_date.asc())
        .limit(5)
        .all()
    )

    return {
        "my_open_cases": my_open_cases,
        "my_active_cases": my_active_cases,
        "my_cases": my_cases,
        "recent_activity": recent_activity,
        "priority_cases": priority_cases,
    }


def _compute_workflow_stats() -> dict:
    """Workflow stats: reminders and SpiderFoot scan metrics."""
    now = datetime.now(timezone.utc)
    overdue_reminders = (
        Reminder.query.filter(
            Reminder.tenant_id == current_user.tenant_id,
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
            Reminder.tenant_id == current_user.tenant_id,
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

    sf_total = apply_tenant_filter(
        SpiderFootScan.query.filter_by(is_deleted=False), SpiderFootScan
    ).count()
    sf_running = apply_tenant_filter(
        SpiderFootScan.query.filter_by(is_deleted=False, status="RUNNING"),
        SpiderFootScan,
    ).count()
    sf_completed = apply_tenant_filter(
        SpiderFootScan.query.filter_by(is_deleted=False, status="FINISHED"),
        SpiderFootScan,
    ).count()
    sf_failed = apply_tenant_filter(
        SpiderFootScan.query.filter_by(is_deleted=False, status="FAILED"),
        SpiderFootScan,
    ).count()
    sf_last_scan = (
        apply_tenant_filter(
            SpiderFootScan.query.filter_by(is_deleted=False),
            SpiderFootScan,
        )
        .order_by(SpiderFootScan.created_at.desc())
        .first()
    )
    sf_last_scan_time = sf_last_scan.created_at.isoformat() if sf_last_scan else None
    sf_health = Setting.get("spiderfoot_health", "")
    sf_last_ok = Setting.get("spiderfoot_last_ok", "")

    return {
        "overdue_reminders": overdue_reminders,
        "upcoming_reminders": upcoming_reminders,
        "sf_total": sf_total,
        "sf_running": sf_running,
        "sf_completed": sf_completed,
        "sf_failed": sf_failed,
        "sf_last_scan_time": sf_last_scan_time,
        "sf_health": sf_health,
        "sf_last_ok": sf_last_ok,
    }


@cms_bp.route("/settings/statistics")
@login_required
@admin_required
def statistics() -> str:
    """Statistics page with all dashboard widgets."""
    case = _compute_case_stats()
    subject = _compute_subject_stats()
    activity = _compute_activity_stats()
    workflow = _compute_workflow_stats()

    return render_template(
        "cms/settings/statistics.html",
        stats=case["stats"],
        my_cases=activity["my_cases"],
        recent_activity=activity["recent_activity"],
        priority_cases=activity["priority_cases"],
        status_labels=case["status_labels"],
        status_values=case["status_values"],
        priority_data=case["priority_data"],
        recent_cases=case["recent_cases"],
        subject_type_labels=subject["subject_type_labels"],
        subject_type_values=subject["subject_type_values"],
        overdue_reminders=workflow["overdue_reminders"],
        upcoming_reminders=workflow["upcoming_reminders"],
        case_type_labels=subject["case_type_labels"],
        case_type_values=subject["case_type_values"],
        investigator_names=subject["investigator_names"],
        investigator_counts=subject["investigator_counts"],
        my_open_cases=activity["my_open_cases"],
        my_active_cases=activity["my_active_cases"],
        overdue_cases=case["overdue_cases"],
        sf_total=workflow["sf_total"],
        sf_running=workflow["sf_running"],
        sf_completed=workflow["sf_completed"],
        sf_failed=workflow["sf_failed"],
        sf_last_scan_time=workflow["sf_last_scan_time"],
        sf_health=workflow["sf_health"],
        sf_last_ok=workflow["sf_last_ok"],
    )
