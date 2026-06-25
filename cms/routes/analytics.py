import json
import logging
from datetime import date, timedelta, datetime, timezone

from flask import render_template, redirect, url_for, flash, abort, request
from flask_login import login_required, current_user

from . import cms_bp
from .. import csrf
from ..auth import viewer_required
from ..models import (
    db,
    UsageRecord,
    Tenant,
    User,
    Case,
    Subject,
    Client,
    Finding,
    Document,
)

logger = logging.getLogger(__name__)


@cms_bp.route("/analytics")
@login_required
@viewer_required
def analytics():
    """Usage analytics dashboard for the current tenant."""
    td = date.today()

    start_30 = td - timedelta(days=30)
    start_7 = td - timedelta(days=7)

    days_30 = (
        UsageRecord.query.filter(
            UsageRecord.tenant_id == current_user.tenant_id,
            UsageRecord.record_date >= start_30,
        )
        .order_by(UsageRecord.record_date, UsageRecord.metric_name)
        .all()
    )

    days_7 = [r for r in days_30 if r.record_date >= start_7]

    def aggregate(records, metric):
        vals = [r for r in records if r.metric_name == metric]
        if not vals:
            return 0, 0, 0
        return (
            vals[-1].metric_value,
            vals[0].metric_value,
            vals[-1].metric_value - vals[0].metric_value,
        )

    stats_7 = {}
    stats_30 = {}
    for m in (
        "users",
        "cases",
        "subjects",
        "clients",
        "findings",
        "documents",
        "audit_log_entries",
    ):
        stats_7[m] = aggregate(days_7, m)
        stats_30[m] = aggregate(days_30, m)

    chart_dates = sorted({r.record_date for r in days_30})
    chart_data = {}
    for m in ("users", "cases", "subjects", "findings", "audit_log_entries"):
        by_date = {}
        for r in days_30:
            if r.metric_name == m:
                by_date[r.record_date] = r.metric_value
        chart_data[m] = [by_date.get(d, 0) for d in chart_dates]

    tenant = db.session.get(Tenant, current_user.tenant_id)
    tier_limits = {}
    if tenant:
        try:
            from ..tier_limits import get_tier_limits

            tier_limits = get_tier_limits(tenant.tier)
        except Exception:
            pass

    latest = {}
    for r in days_30:
        latest[r.metric_name] = r.metric_value

    return render_template(
        "cms/analytics.html",
        stats_7=stats_7,
        stats_30=stats_30,
        chart_dates=chart_dates,
        chart_data=chart_data,
        tier_limits=tier_limits,
        latest=latest,
        days_30=days_30,
    )


@cms_bp.route("/analytics/aggregate", methods=["GET", "POST"])
@login_required
@viewer_required
@csrf.exempt
def aggregate_usage_now():
    """Super-admin: manually trigger usage aggregation."""
    if request.method == "GET":
        return redirect(url_for("cms.analytics"))
    if not current_user.is_super_admin:
        flash("Only super admins can trigger aggregation.", "error")
        return redirect(url_for("cms.analytics"))
    from ..aggregation import aggregate_all_tenants

    try:
        count = aggregate_all_tenants()
        flash(f"Aggregated usage for {count} tenants.", "success")
    except Exception as e:
        logger.exception("Manual aggregation failed")
        flash(f"Aggregation failed: {e}", "error")
    return redirect(url_for("cms.analytics"))


@cms_bp.route("/analytics/super")
@login_required
@viewer_required
def analytics_super():
    """Super-admin aggregate overview of all tenants."""
    if not current_user.is_super_admin:
        abort(403)

    td = date.today()
    start_30 = td - timedelta(days=30)

    total_tenants = Tenant.query.count()
    active_tenants = Tenant.query.filter_by(is_active=True).count()
    inactive_tenants = total_tenants - active_tenants

    tier_counts = dict(
        db.session.query(Tenant.tier, db.func.count(Tenant.id))
        .group_by(Tenant.tier)
        .all()
    )

    sub_counts = dict(
        db.session.query(Tenant.subscription_status, db.func.count(Tenant.id))
        .group_by(Tenant.subscription_status)
        .all()
    )

    totals = {
        "users": User.query.count(),
        "cases": Case.query.filter_by(is_deleted=False).count(),
        "subjects": Subject.query.filter_by(is_deleted=False).count(),
        "clients": Client.query.filter_by(is_deleted=False, is_active=True).count(),
        "findings": Finding.query.filter_by(is_deleted=False).count(),
        "documents": Document.query.filter_by(is_deleted=False).count(),
    }

    recent_tenants = Tenant.query.order_by(Tenant.created_at.desc()).limit(10).all()

    new_tenants_30d = Tenant.query.filter(
        Tenant.created_at >= datetime.now(timezone.utc) - timedelta(days=30)
    ).count()

    top_by_users = (
        db.session.query(
            Tenant.id, Tenant.name, Tenant.tier, db.func.count(User.id).label("cnt")
        )
        .join(User, User.tenant_id == Tenant.id, isouter=True)
        .group_by(Tenant.id)
        .order_by(db.desc("cnt"))
        .limit(10)
        .all()
    )

    top_by_cases = (
        db.session.query(
            Tenant.id, Tenant.name, Tenant.tier, db.func.count(Case.id).label("cnt")
        )
        .join(Case, Case.tenant_id == Tenant.id, isouter=True)
        .filter(db.or_(Case.is_deleted == False, Case.is_deleted.is_(None)))
        .group_by(Tenant.id)
        .order_by(db.desc("cnt"))
        .limit(10)
        .all()
    )

    usage = {}
    latest_records = UsageRecord.query.filter(UsageRecord.record_date == td).all()
    if not latest_records:
        latest_records = UsageRecord.query.filter(
            UsageRecord.record_date >= start_30
        ).all()
        by_tenant = {}
        for r in latest_records:
            by_tenant[r.tenant_id] = by_tenant.get(r.tenant_id, {})
            by_tenant[r.tenant_id][r.metric_name] = r.metric_value
        for tid, metrics in by_tenant.items():
            t = db.session.get(Tenant, tid)
            if t:
                usage[tid] = {"name": t.name, **metrics}
    else:
        for r in latest_records:
            if r.tenant_id not in usage:
                t = db.session.get(Tenant, r.tenant_id)
                if not t:
                    continue
                usage[r.tenant_id] = {"name": t.name}
            usage[r.tenant_id][r.metric_name] = r.metric_value

    try:
        from ..models import PlatformSetting

        price_mapping_raw = PlatformSetting.get("stripe_price_mapping")
        price_mapping = json.loads(price_mapping_raw) if price_mapping_raw else {}
    except Exception:
        price_mapping = {}

    DEFAULT_MONTHLY_PRICES = {
        "free": 0,
        "starter": 29,
        "professional": 99,
        "enterprise": 0,
    }
    tier_prices = {}
    for tier in ("free", "starter", "professional", "enterprise"):
        price_id = price_mapping.get(tier) if isinstance(price_mapping, dict) else None
        tier_prices[tier] = DEFAULT_MONTHLY_PRICES.get(tier, 0)

    mrr = sum(tier_counts.get(tier, 0) * price for tier, price in tier_prices.items())
    arr = mrr * 12

    sub_active = sub_counts.get("active", 0)
    sub_canceled = sub_counts.get("canceled", 0) + sub_counts.get(
        "incomplete_expired", 0
    )
    total_sub_tenants = Tenant.query.filter(
        Tenant.stripe_subscription_id.isnot(None)
    ).count()
    churn_rate = (
        round(sub_canceled / (sub_active + sub_canceled) * 100, 1)
        if (sub_active + sub_canceled) > 0
        else 0
    )

    canceled_30d = Tenant.query.filter(
        Tenant.subscription_status == "canceled",
        Tenant.updated_at >= datetime.now(timezone.utc) - timedelta(days=30),
    ).count()

    return render_template(
        "cms/analytics_super.html",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        inactive_tenants=inactive_tenants,
        tier_counts=tier_counts,
        sub_counts=sub_counts,
        totals=totals,
        recent_tenants=recent_tenants,
        new_tenants_30d=new_tenants_30d,
        top_by_users=top_by_users,
        top_by_cases=top_by_cases,
        usage=usage,
        price_mapping=price_mapping,
        mrr=mrr,
        arr=arr,
        churn_rate=churn_rate,
        canceled_30d=canceled_30d,
    )
