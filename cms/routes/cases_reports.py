import logging
from datetime import datetime, timedelta

import flask
from flask import abort, jsonify, render_template, request
from flask_login import login_required

from ..auth import apply_tenant_filter, audit_read, can_export, case_access_required
from ..models import (
    AuditLog,
    Case,
    Comment,
    Document,
    FinancialRecord,
    Finding,
    Reminder,
    Subject,
    db,
)
from . import cms_bp

logger = logging.getLogger(__name__)


@cms_bp.route("/cases/<case_id>/export-json", methods=["GET"])
@login_required
@case_access_required
@can_export
def export_case_json(case_id: str) -> flask.Response:
    """Export a case as JSON with all relations."""
    import json as json_mod

    case = db.session.get(Case, case_id) or abort(404)
    export_data = case.to_dict()
    export_data["subjects"] = [
        s.to_dict() for s in case.subjects.filter(Subject.is_deleted == False)
    ]
    export_data["findings"] = [
        f.to_dict()
        for f in Finding.query.filter_by(case_id=case_id)
        .filter(Finding.is_deleted == False, Finding.archived_at.is_(None))
        .all()
    ]
    export_data["documents"] = [
        d.to_dict()
        for d in Document.query.filter_by(case_id=case_id, is_deleted=False).all()
    ]
    export_data["comments"] = [
        c.to_dict()
        for c in Comment.query.filter_by(case_id=case_id, is_deleted=False).all()
    ]
    export_data["financials"] = [
        r.to_dict()
        for r in FinancialRecord.query.filter_by(
            case_id=case_id, is_deleted=False
        ).all()
    ]
    return flask.Response(
        json_mod.dumps(export_data, indent=2, default=str),
        mimetype="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=case_{case.case_number}.json"
        },
    )


@cms_bp.route("/cases/<case_id>")
@login_required
@case_access_required
@audit_read("case")
def view_case(case_id: str) -> str:
    """View case details with subjects, findings, and financials."""
    case = db.session.get(Case, case_id) or abort(404)
    subjects = case.subjects.filter(Subject.is_deleted == False).all()
    child_cases = case.child_cases.filter_by(is_deleted=False).all()

    # Decrypt subject identifiers so the template shows plaintext, not ciphertext
    with db.session.no_autoflush:
        for s in subjects:
            s.decrypt_identifiers()

    findings_page = request.args.get("findings_page", 1, type=int)
    findings_per_page = 20
    findings_pagination = (
        case.findings.filter(Finding.is_deleted == False, Finding.archived_at.is_(None))
        .order_by(Finding.created_at.desc())
        .paginate(page=findings_page, per_page=findings_per_page, error_out=False)
    )

    financials_page = request.args.get("financials_page", 1, type=int)
    financials_per_page = 20
    financials_pagination = (
        case.financial_records.filter_by(is_deleted=False)
        .order_by(FinancialRecord.transaction_date.desc())
        .paginate(page=financials_page, per_page=financials_per_page, error_out=False)
    )

    documents_page = request.args.get("documents_page", 1, type=int)
    documents_per_page = 20
    documents_pagination = (
        Document.query.filter_by(case_id=case_id, is_deleted=False)
        .order_by(Document.created_at.desc())
        .paginate(page=documents_page, per_page=documents_per_page, error_out=False)
    )

    case_reminders = (
        Reminder.query.filter_by(case_id=case_id, is_deleted=False)
        .order_by(Reminder.reminder_date.asc())
        .all()
    )

    linked_ids = [s.id for s in subjects]
    all_subjects = (
        apply_tenant_filter(
            Subject.query.filter(
                Subject.is_deleted == False, ~Subject.id.in_(linked_ids)
            ),
            Subject,
        )
        .limit(500)
        .all()
    )
    for s in all_subjects:
        s.decrypt_identifiers()
    available_subjects = all_subjects

    return render_template(
        "cms/cases/view.html",
        case=case,
        subjects=subjects,
        findings=findings_pagination.items,
        findings_pagination=findings_pagination,
        financials=financials_pagination.items,
        financials_pagination=financials_pagination,
        documents=documents_pagination.items,
        documents_pagination=documents_pagination,
        all_subjects=available_subjects,
        case_reminders=case_reminders,
        child_cases=child_cases,
    )


@cms_bp.route("/cases/<case_id>/timeline")
@login_required
@case_access_required
def case_timeline(case_id: str) -> flask.Response:
    """Get timeline of all events for a case."""
    case = db.session.get(Case, case_id) or abort(404)

    timeline = []

    timeline.append(
        {
            "timestamp": case.created_at,
            "type": "create",
            "icon": "📁",
            "title": "Case Created",
            "description": f"{case.case_number} - {case.title}",
            "user": None,
            "details": f"Client: {case.client.name if case.client else 'N/A'}",
        }
    )

    status_logs = (
        AuditLog.query.filter(
            AuditLog.entity_type == "case",
            AuditLog.entity_id == case_id,
            AuditLog.action.in_(["update", "status_change"]),
        )
        .order_by(AuditLog.timestamp.asc())
        .all()
    )

    for log in status_logs:
        if log.changes_made and "status" in str(log.changes_made):
            timeline.append(
                {
                    "timestamp": log.timestamp,
                    "type": "status",
                    "icon": "🔄",
                    "title": "Status Changed",
                    "description": log.description or "Case status updated",
                    "user": log.user,
                    "details": log.changes_made,
                }
            )

    subject_add_logs = (
        AuditLog.query.filter(
            AuditLog.case_id == case_id,
            AuditLog.action == "create",
            AuditLog.entity_type == "case_subject",
        )
        .order_by(AuditLog.timestamp.asc())
        .all()
    )

    for log in subject_add_logs:
        timeline.append(
            {
                "timestamp": log.timestamp,
                "type": "subject",
                "icon": "👤",
                "title": "Subject Added",
                "description": log.description or "Subject linked to case",
                "user": log.user,
                "details": None,
            }
        )

    for finding in (
        case.findings.filter_by(is_deleted=False)
        .order_by(Finding.created_at.asc())
        .all()
    ):
        timeline.append(
            {
                "timestamp": finding.created_at,
                "type": "finding",
                "icon": "🔍",
                "title": "Finding Added",
                "description": finding.title[:100]
                + ("..." if len(finding.title) > 100 else ""),
                "user": finding.author,
                "details": f"Source: {finding.source_type or 'manual'}",
            }
        )

    osint_logs = (
        AuditLog.query.filter(
            AuditLog.case_id == case_id,
            AuditLog.action.in_(["osint_search_start", "osint_search_cancel"]),
        )
        .order_by(AuditLog.timestamp.asc())
        .all()
    )

    for log in osint_logs:
        icon = "🔍" if log.action == "osint_search_start" else "⏹️"
        timeline.append(
            {
                "timestamp": log.timestamp,
                "type": "osint",
                "icon": icon,
                "title": "OSINT Search"
                if log.action == "osint_search_start"
                else "OSINT Search Cancelled",
                "description": log.description or "OSINT search performed",
                "user": log.user,
                "details": None,
            }
        )

    for fin in (
        case.financial_records.filter_by(is_deleted=False)
        .order_by(FinancialRecord.created_at.asc())
        .all()
    ):
        timeline.append(
            {
                "timestamp": fin.created_at,
                "type": "financial",
                "icon": "💰",
                "title": "Financial Record",
                "description": f"{fin.currency} {fin.amount} - {fin.transaction_type or 'Transaction'}",
                "user": None,
                "details": f"Source: {fin.source or 'N/A'}",
            }
        )

    if case.reopened_at:
        timeline.append(
            {
                "timestamp": case.reopened_at,
                "type": "reopen",
                "icon": "↩️",
                "title": "Case Reopened",
                "description": f"Reopened: {case.reopened_reason or 'No reason provided'}",
                "user": None,
                "details": None,
            }
        )

    if case.actual_end_date:
        timeline.append(
            {
                "timestamp": datetime.combine(
                    case.actual_end_date, datetime.min.time()
                ),
                "type": "close",
                "icon": "✅",
                "title": "Case Closed",
                "description": f"Closure: {case.closure_reason or 'No reason provided'}",
                "user": None,
                "details": None,
            }
        )

    timeline.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)

    return jsonify(
        {
            "case_id": case_id,
            "case_number": case.case_number,
            "title": case.title,
            "timeline": [
                {
                    "timestamp": t["timestamp"].isoformat() if t["timestamp"] else None,
                    "type": t["type"],
                    "icon": t["icon"],
                    "title": t["title"],
                    "description": t["description"],
                    "user_name": t["user"].full_name if t["user"] else "System",
                    "details": t["details"],
                }
                for t in timeline
            ],
        }
    )


@cms_bp.route("/cases/<case_id>/report")
@login_required
@case_access_required
def case_report(case_id: str) -> str:
    """Chronological report merging Findings + Comments for a case."""
    case = db.session.get(Case, case_id) or abort(404)
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    subject_filter = request.args.get("subject_id")

    findings_q = Finding.query.filter_by(case_id=case_id, is_deleted=False)
    comments_q = Comment.query.filter_by(case_id=case_id, is_deleted=False)

    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d")
            findings_q = findings_q.filter(Finding.created_at >= fd)
            comments_q = comments_q.filter(Comment.created_at >= fd)
        except ValueError:
            logger.debug("Invalid from_date filter: %s", from_date)
        if to_date:
            try:
                td = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                findings_q = findings_q.filter(Finding.created_at < td)
                comments_q = comments_q.filter(Comment.created_at < td)
            except ValueError:
                logger.debug("Invalid to_date filter: %s", to_date)
        findings_q = findings_q.filter_by(subject_id=subject_filter)
        comments_q = comments_q.filter_by(subject_id=subject_filter)

    findings = (
        findings_q.options(db.joinedload(Finding.author))
        .order_by(Finding.created_at.asc())
        .all()
    )
    comments = (
        comments_q.options(db.joinedload(Comment.author))
        .order_by(Comment.created_at.asc())
        .all()
    )

    # Batch-load subjects for findings + comments
    all_subject_ids = set()
    for f in findings:
        if f.subject_id:
            all_subject_ids.add(f.subject_id)
    for c in comments:
        if c.subject_id:
            all_subject_ids.add(c.subject_id)
    subjects_map = {}
    if all_subject_ids:
        for s in apply_tenant_filter(
            Subject.query.filter(Subject.id.in_(all_subject_ids)), Subject
        ).all():
            subjects_map[s.id] = s

    entries = []
    for f in findings:
        subject = subjects_map.get(f.subject_id)
        entries.append(
            {
                "type": "finding",
                "icon": "🔍",
                "timestamp": f.created_at,
                "title": f.title,
                "content": f.content,
                "source_type": f.source_type,
                "confidence": f.confidence_level,
                "source_url": f.source_url,
                "author": f.author.full_name if f.author else "-",
                "subject_name": subject.name if subject else "-",
                "subject_id": f.subject_id,
                "finding_type": f.finding_type,
            }
        )
    for c in comments:
        subject = subjects_map.get(c.subject_id)
        entries.append(
            {
                "type": "note",
                "icon": "📝" if c.comment_type == "note" else "💬",
                "timestamp": c.created_at,
                "title": c.comment_type.capitalize(),
                "content": c.content,
                "source_type": c.comment_type,
                "confidence": None,
                "source_url": None,
                "author": c.author.full_name if c.author else "-",
                "subject_name": subject.name if subject else "-",
                "subject_id": c.subject_id,
                "finding_type": None,
            }
        )

    entries.sort(key=lambda e: e["timestamp"] or datetime.min)

    grouped = {}
    for e in entries:
        date_key = e["timestamp"].strftime("%Y-%m-%d") if e["timestamp"] else "Unknown"
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(e)

    subjects = apply_tenant_filter(
        Subject.query.filter(
            Subject.cases.any(id=case_id), Subject.is_deleted == False
        ),
        Subject,
    ).all()

    return render_template(
        "cms/cases/report.html",
        case=case,
        grouped_entries=grouped,
        subjects=subjects,
        from_date=from_date,
        to_date=to_date,
        subject_filter=subject_filter,
    )


@cms_bp.route("/cases/<case_id>/report-pdf")
@login_required
@case_access_required
def case_report_pdf(case_id: str) -> flask.Response:
    """Generate a server-side PDF report for a case using WeasyPrint."""
    from datetime import datetime as dt_mod

    case = db.session.get(Case, case_id) or abort(404)

    findings_q = Finding.query.filter_by(case_id=case_id, is_deleted=False)
    comments_q = Comment.query.filter_by(case_id=case_id, is_deleted=False)

    findings = (
        findings_q.options(db.joinedload(Finding.author))
        .order_by(Finding.created_at.asc())
        .all()
    )
    comments = (
        comments_q.options(db.joinedload(Comment.author))
        .order_by(Comment.created_at.asc())
        .all()
    )

    all_subject_ids = set()
    for f in findings:
        if f.subject_id:
            all_subject_ids.add(f.subject_id)
    for c in comments:
        if c.subject_id:
            all_subject_ids.add(c.subject_id)
    subjects_map = {}
    if all_subject_ids:
        for s in apply_tenant_filter(
            Subject.query.filter(Subject.id.in_(all_subject_ids)), Subject
        ).all():
            subjects_map[s.id] = s

    entries = []
    for f in findings:
        subject = subjects_map.get(f.subject_id)
        entries.append(
            {
                "type": "finding",
                "icon": "🔍",
                "timestamp": f.created_at,
                "title": f.title,
                "content": f.content,
                "source_type": f.source_type,
                "source_url": f.source_url,
                "author": f.author.full_name if f.author else "-",
                "subject_name": subject.name if subject else "-",
            }
        )
    for c in comments:
        subject = subjects_map.get(c.subject_id)
        entries.append(
            {
                "type": "note",
                "icon": "📝" if c.comment_type == "note" else "💬",
                "timestamp": c.created_at,
                "title": c.comment_type.capitalize(),
                "content": c.content,
                "source_type": c.comment_type,
                "source_url": None,
                "author": c.author.full_name if c.author else "-",
                "subject_name": subject.name if subject else "-",
            }
        )

    entries.sort(key=lambda e: e["timestamp"] or dt_mod.min)

    grouped = {}
    for e in entries:
        date_key = e["timestamp"].strftime("%Y-%m-%d") if e["timestamp"] else "Onbekend"
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(e)

    subjects = apply_tenant_filter(
        Subject.query.filter(
            Subject.cases.any(id=case_id), Subject.is_deleted == False
        ),
        Subject,
    ).all()

    now_func = dt_mod.now

    html = flask.render_template(
        "cms/cases/report_pdf.html",
        case=case,
        grouped_entries=grouped,
        subjects=subjects,
        now=now_func,
    )

    try:
        from weasyprint import HTML as WPHTML

        pdf_data = WPHTML(string=html).write_pdf()
    except Exception as e:
        logger.error("WeasyPrint PDF generation failed: %s", e)
        flask.flash(
            "PDF generation failed. Try the browser Print/PDF function.", "error"
        )
        return flask.redirect(flask.url_for("cms.case_report", case_id=case_id))

    # Sanitize filename
    safe_num = case.case_number.replace("/", "-").replace("\\", "-") or case.id[:8]
    return flask.Response(
        pdf_data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=rapport_{safe_num}.pdf"},
    )
