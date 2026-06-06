import logging
from datetime import datetime, timezone, timedelta

import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from ..validation import validate, CreateReminderSchema, EditReminderSchema
from ..models import (
    db,
    Reminder,
    ReminderType,
    ReminderRecurrence,
    User,
    Case,
    Subject,
    Client,
    AuditLog,
)

logger = logging.getLogger(__name__)


@cms_bp.route("/reminders")
@login_required
def reminders() -> str:
    """List all reminders for current user."""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    filter_type = request.args.get("filter", "all")

    query = Reminder.query.filter(Reminder.is_deleted == False).options(
        joinedload(Reminder.assigned_user)
    )

    # Filter by status
    if filter_type == "overdue":
        query = query.filter(
            Reminder.is_completed == False,
            Reminder.reminder_date < datetime.now(timezone.utc),
        )
    elif filter_type == "upcoming":
        query = query.filter(
            Reminder.is_completed == False,
            Reminder.reminder_date >= datetime.now(timezone.utc),
        )
    elif filter_type == "completed":
        query = query.filter(Reminder.is_completed == True)
    elif filter_type == "mine":
        query = query.filter(Reminder.assigned_to == current_user.id)

    pagination = query.order_by(Reminder.reminder_date.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Stats — single aggregated query
    now = datetime.now(timezone.utc)
    row = db.session.query(
        func.count(Reminder.id)
        .filter(Reminder.is_deleted == False, Reminder.is_completed == False)
        .label("total"),
        func.count(Reminder.id)
        .filter(
            Reminder.is_deleted == False,
            Reminder.is_completed == False,
            Reminder.reminder_date < now,
        )
        .label("overdue"),
        func.count(Reminder.id)
        .filter(
            Reminder.is_deleted == False,
            Reminder.is_completed == False,
            db.func.date(Reminder.reminder_date) == now.date(),
        )
        .label("today"),
    ).first()
    stats = {
        "total": row.total if row else 0,
        "overdue": row.overdue if row else 0,
        "today": row.today if row else 0,
    }

    return render_template(
        "cms/reminders/list.html",
        reminders=pagination.items,
        pagination=pagination,
        filter_type=filter_type,
        stats=stats,
    )


@cms_bp.route("/reminders/create", methods=["GET", "POST"])
@login_required
@validate(CreateReminderSchema)
def create_reminder() -> flask.Response:
    """Create a new reminder."""
    # Get related entities if specified
    case_id = request.args.get("case_id")
    subject_id = request.args.get("subject_id")
    client_id = request.args.get("client_id")

    case = db.session.get(Case, case_id) if case_id else None
    subject = db.session.get(Subject, subject_id) if subject_id else None
    client = db.session.get(Client, client_id) if client_id else None

    # Get users for assignment dropdown
    users = User.query.filter_by(is_active=True).all()

    # Calculate default reminder date (1 hour from now)
    default_reminder = datetime.now(timezone.utc) + timedelta(hours=1)
    default_reminder_date = default_reminder.strftime("%Y-%m-%dT%H:%M")

    if request.method == "POST":
        data = request.validated_data

        title = data.get("title")
        if not title:
            if request.is_json:
                return jsonify({"error": "Title is required"}), 400
            flash("Title is required.", "danger")
            return render_template(
                "cms/reminders/create.html",
                case=case,
                subject=subject,
                client=client,
                users=users,
                default_reminder_date=default_reminder_date,
            )

        # Parse reminder date
        reminder_date_str = data.get("reminder_date")
        if reminder_date_str:
            try:
                reminder_date = datetime.strptime(reminder_date_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                try:
                    reminder_date = datetime.strptime(
                        reminder_date_str, "%Y-%m-%d %H:%M"
                    )
                except ValueError:
                    reminder_date = datetime.now(timezone.utc)
        else:
            reminder_date = datetime.now(timezone.utc)

        # Parse due date if provided
        due_date_str = data.get("due_date")
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            except ValueError:
                due_date = None

        reminder = Reminder(
            title=title,
            description=data.get("description", ""),
            reminder_date=reminder_date,
            due_date=due_date,
            reminder_type=data.get("reminder_type", ReminderType.MANUAL.value),
            recurrence=data.get("recurrence", ReminderRecurrence.NONE.value),
            priority=data.get("priority", "medium"),
            case_id=data.get("case_id") or case_id,
            subject_id=data.get("subject_id") or subject_id,
            client_id=data.get("client_id") or client_id,
            assigned_to=data.get("assigned_to") or None,
            created_by=current_user.id,
            notify_email=data.get("notify_email") in ["on", "true", "1", True],
            notify_dashboard=data.get("notify_dashboard") in ["on", "true", "1", True],
        )

        db.session.add(reminder)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="reminder",
            entity_id=reminder.id,
            ip_address=request.remote_addr,
            description=f"Created reminder: {reminder.title}",
        )
        db.session.commit()

        if request.is_json:
            return jsonify(
                {"message": "Reminder created", "reminder": reminder.to_dict()}
            ), 201

        flash("Reminder created.", "success")
        return redirect(url_for("cms.reminders"))

    return render_template(
        "cms/reminders/create.html",
        case=case,
        subject=subject,
        client=client,
        users=users,
        default_reminder_date=default_reminder_date,
    )


@cms_bp.route("/reminders/<reminder_id>")
@login_required
def view_reminder(reminder_id: str) -> str:
    """View reminder details."""
    reminder = db.session.get(Reminder, reminder_id) or abort(404)

    # Get related case if available
    case = db.session.get(Case, reminder.case_id) if reminder.case_id else None

    AuditLog.log(
        user_id=current_user.id,
        action="read",
        entity_type="reminder",
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Viewed reminder: {reminder.title}",
    )
    db.session.commit()

    return render_template("cms/reminders/view.html", reminder=reminder, case=case)


@cms_bp.route("/reminders/<reminder_id>/edit", methods=["GET", "POST"])
@login_required
@validate(EditReminderSchema)
def edit_reminder(reminder_id: str) -> flask.Response:
    """Edit a reminder."""
    reminder = db.session.get(Reminder, reminder_id) or abort(404)
    users = User.query.filter_by(is_active=True).all()

    if request.method == "POST":
        data = request.validated_data

        old_values = {}
        changes = {}

        # Update fields
        fields = [
            "title",
            "description",
            "priority",
            "reminder_type",
            "recurrence",
            "assigned_to",
        ]
        for field in fields:
            if field in data:
                old_val = getattr(reminder, field)
                new_val = data[field]
                if old_val != new_val:
                    old_values[field] = old_val
                    changes[field] = {"old": old_val, "new": new_val}
                    setattr(reminder, field, new_val)

        # Parse and update dates
        due_date_str = data.get("due_date")
        reminder_date_str = data.get("reminder_date")
        if reminder_date_str:
            try:
                reminder.reminder_date = datetime.strptime(
                    reminder_date_str, "%Y-%m-%dT%H:%M"
                )
            except ValueError:
                logger.debug("Invalid reminder date format: %s", reminder_date_str)
        if due_date_str:
            try:
                reminder.due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            except ValueError:
                reminder.due_date = None
        elif "due_date" in data and not due_date_str:
            reminder.due_date = None

        # Update notification settings
        reminder.notify_email = data.get("notify_email") in ["on", "true", "1", True]
        reminder.notify_dashboard = data.get("notify_dashboard") in [
            "on",
            "true",
            "1",
            True,
        ]

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="reminder",
            entity_id=reminder_id,
            changes=changes if changes else None,
            ip_address=request.remote_addr,
            description=f"Updated reminder: {reminder.title}",
        )
        db.session.commit()

        if request.is_json:
            return jsonify(
                {"message": "Reminder updated", "reminder": reminder.to_dict()}
            )

        flash("Reminder updated.", "success")
        return redirect(url_for("cms.view_reminder", reminder_id=reminder_id))

    return render_template("cms/reminders/edit.html", reminder=reminder, users=users)


@cms_bp.route("/reminders/<reminder_id>/complete", methods=["POST"])
@login_required
def complete_reminder(reminder_id: str) -> flask.Response:
    """Mark a reminder as completed."""
    reminder = db.session.get(Reminder, reminder_id) or abort(404)

    reminder.complete()

    AuditLog.log(
        user_id=current_user.id,
        action="complete",
        entity_type="reminder",
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Completed reminder: {reminder.title}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify(
            {"message": "Reminder completed", "reminder": reminder.to_dict()}
        )

    flash("Reminder marked as completed.", "success")

    # Check if there's a return URL
    return_url = request.args.get("return_url")
    if return_url:
        return redirect(return_url)
    return redirect(url_for("cms.reminders"))


@cms_bp.route("/reminders/<reminder_id>/snooze", methods=["POST"])
@login_required
def snooze_reminder(reminder_id: str) -> flask.Response:
    """Snooze a reminder."""
    reminder = db.session.get(Reminder, reminder_id) or abort(404)

    minutes = request.args.get("minutes", 30, type=int)
    reminder.snooze(minutes=minutes)

    AuditLog.log(
        user_id=current_user.id,
        action="snooze",
        entity_type="reminder",
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Snoozed reminder: {reminder.title} for {minutes} minutes",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"message": "Reminder snoozed", "reminder": reminder.to_dict()})

    flash(f"Reminder snoozed for {minutes} minutes.", "info")

    return_url = request.args.get("return_url")
    if return_url:
        return redirect(return_url)
    return redirect(url_for("cms.reminders"))


@cms_bp.route("/reminders/<reminder_id>/delete", methods=["POST"])
@login_required
def delete_reminder(reminder_id: str) -> flask.Response:
    """Delete a reminder."""
    reminder = db.session.get(Reminder, reminder_id) or abort(404)

    reminder.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="reminder",
        entity_id=reminder_id,
        ip_address=request.remote_addr,
        description=f"Deleted reminder: {reminder.title}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"message": "Reminder deleted"})

    flash("Reminder deleted.", "info")
    return redirect(url_for("cms.reminders"))


@cms_bp.route("/api/reminders/check-overdue")
@login_required
def api_check_overdue() -> flask.Response:
    """API endpoint to check and update overdue reminders."""
    now = datetime.now(timezone.utc)

    overdue = Reminder.query.filter(
        Reminder.is_deleted == False,
        Reminder.is_completed == False,
        Reminder.reminder_date < now,
    ).all()

    count = 0
    for r in overdue:
        if not r.is_overdue:
            r.is_overdue = True
            count += 1

    if count > 0:
        db.session.commit()

    return jsonify({"overdue_count": len(overdue), "newly_overdue": count})
