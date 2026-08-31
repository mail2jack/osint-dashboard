import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Case, Client, Subject, Finding, CaseStatus
from ..auth import apply_tenant_filter
from ..health_utils import check_external_services

logger = logging.getLogger(__name__)

_health_cache: dict[str, object] = {}
_health_cache_lock = threading.Lock()
_HEALTH_CACHE_TTL = 300
_HEALTH_REFRESH_INTERVAL = 300
_health_monitor_started = False


def _refresh_health(app) -> None:
    timings: dict[str, float] = {}
    started = time.monotonic()
    try:
        with app.app_context():
            fresh = check_external_services(timings=timings)
        snapshot = {
            "services": fresh,
            "timings_ms": timings,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
        with _health_cache_lock:
            _health_cache["health"] = snapshot
    except Exception:
        logger.exception("Background health refresh failed")


def _health_monitor(app) -> None:
    while True:
        _refresh_health(app)
        time.sleep(_HEALTH_REFRESH_INTERVAL)


def init_health_monitor(app) -> None:
    """Refresh full health outside the Gunicorn request worker."""
    global _health_monitor_started
    if (
        _health_monitor_started
        or app.testing
        or os.environ.get("FLASK_ENV", "development") != "production"
    ):
        return
    _health_monitor_started = True
    thread = threading.Thread(
        target=_health_monitor,
        args=(app,),
        daemon=True,
        name="health-refresh",
    )
    thread.start()


def _get_cached_health() -> dict:
    with _health_cache_lock:
        snapshot = _health_cache.get("health")
    if not isinstance(snapshot, dict):
        return {
            "services": {"database": "unknown", "spiderfoot": "unknown"},
            "timings_ms": {},
            "checked_at": None,
            "duration_ms": None,
            "stale": True,
            "age_seconds": None,
        }

    checked_at = snapshot.get("checked_at")
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
        **snapshot,
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

    if len(red) == 0 and len(orange) == 0:
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
        }
    )
