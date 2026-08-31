import logging
import json
from datetime import datetime, timezone

from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Case, Client, Subject, Finding, CaseStatus, Setting
from ..auth import apply_tenant_filter

logger = logging.getLogger(__name__)

_HEALTH_CACHE_TTL = 300


def _unavailable_health() -> dict:
    return {
        "services": {"database": "unavailable", "spiderfoot": "unavailable"},
        "timings_ms": {},
        "checked_at": None,
        "duration_ms": None,
        "stale": True,
        "age_seconds": None,
        "refresh_status": "unavailable",
        "refresh_error": None,
    }


def _get_cached_health() -> dict:
    try:
        raw = Setting.get("health_snapshot", "")
    except Exception:
        return _unavailable_health()
    try:
        snapshot = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        snapshot = None
    if not isinstance(snapshot, dict):
        return _unavailable_health()

    services = snapshot.get("services")
    timings = snapshot.get("timings_ms", {})
    if not isinstance(services, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in services.items()
    ):
        return _unavailable_health()
    if not isinstance(timings, dict) or not all(
        isinstance(key, str) and isinstance(value, (int, float))
        for key, value in timings.items()
    ):
        return _unavailable_health()
    checked_at_value = snapshot.get("checked_at")
    if not isinstance(checked_at_value, str) or not checked_at_value:
        return _unavailable_health()

    checked_at = checked_at_value
    age_seconds = None
    if checked_at:
        try:
            checked = datetime.fromisoformat(str(checked_at))
            age_seconds = max(
                0, (datetime.now(timezone.utc) - checked).total_seconds()
            )
        except ValueError:
            age_seconds = None
    return {
        "services": services,
        "timings_ms": timings,
        "checked_at": checked_at,
        "duration_ms": snapshot.get("duration_ms"),
        "refresh_status": snapshot.get("refresh_status", "success"),
        "refresh_error": snapshot.get("refresh_error"),
        "stale": age_seconds is None or age_seconds >= _HEALTH_CACHE_TTL,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
    }


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
    snapshot = _get_cached_health()
    health = snapshot["services"]
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

    if snapshot["stale"]:
        state = "stale"
    elif len(red) == 0 and len(orange) == 0:
        state = "green"
    elif len(red) == len(health):
        state = "red"
    else:
        state = "orange"

    return jsonify(
        {
            "state": state,
            "services": health,
            "checked_at": snapshot["checked_at"],
            "age_seconds": snapshot["age_seconds"],
            "stale": snapshot["stale"],
            "timings_ms": snapshot["timings_ms"],
            "duration_ms": snapshot["duration_ms"],
            "refresh_status": snapshot["refresh_status"],
            "refresh_error": snapshot["refresh_error"],
        }
    )
