"""
SaaS tier limits — max users, max cases, max resources, storage, and feature gating per plan.
"""

from typing import Any

TIERS: dict[str, dict[str, Any]] = {
    "free": {
        "max_users": 2,
        "max_cases": 5,
        "max_subjects": 10,
        "max_clients": 5,
        "max_findings": 25,
        "max_documents": 25,
        "max_storage_mb": 50,
        "max_concurrent_spiderfoot_scans": 0,
        "features": {
            "export": False,
            "ai": False,
            "spiderfoot": False,
            "api_keys": False,
        },
    },
    "starter": {
        "max_users": 5,
        "max_cases": 50,
        "max_subjects": 100,
        "max_clients": 25,
        "max_findings": 500,
        "max_documents": 250,
        "max_storage_mb": 500,
        "max_concurrent_spiderfoot_scans": 0,
        "features": {
            "export": True,
            "ai": False,
            "spiderfoot": False,
            "api_keys": False,
        },
    },
    "professional": {
        "max_users": 25,
        "max_cases": 500,
        "max_subjects": 1000,
        "max_clients": 100,
        "max_findings": 5000,
        "max_documents": 2500,
        "max_storage_mb": 5120,
        "max_concurrent_spiderfoot_scans": 3,
        "features": {
            "export": True,
            "ai": True,
            "spiderfoot": True,
            "api_keys": True,
        },
    },
    "enterprise": {
        "max_users": None,
        "max_cases": None,
        "max_subjects": None,
        "max_clients": None,
        "max_findings": None,
        "max_documents": None,
        "max_storage_mb": None,
        "max_concurrent_spiderfoot_scans": 10,
        "features": {
            "export": True,
            "ai": True,
            "spiderfoot": True,
            "api_keys": True,
        },
    },
}

TIER_DISPLAY = {
    "free": "Free",
    "starter": "Starter",
    "professional": "Professional",
    "enterprise": "Enterprise",
}

# Map metric names used in UsageRecord / aggregation to tier limit keys
METRIC_TO_LIMIT_KEY = {
    "users": "max_users",
    "cases": "max_cases",
    "subjects": "max_subjects",
    "clients": "max_clients",
    "findings": "max_findings",
    "documents": "max_documents",
}


def _get_limits(tier_name: str) -> dict:
    return TIERS.get(tier_name, TIERS["free"])


def get_tier_limits(tier_name: str) -> dict[str, int | None]:
    """Return a flat dict of resource limits for a given tier.

    Keys match metric names ('users', 'cases', 'subjects', etc.)
    so the analytics template can look up ``tier_limits.get('users')``.
    """
    limits = _get_limits(tier_name)
    return {key: limits.get(f"max_{key}") for key in METRIC_TO_LIMIT_KEY}


def check_feature(feature: str, tenant_id: str | None = None) -> bool:
    """Check if the current tenant's tier allows a feature.

    Checks the install license first (soft trial gates AI/SpiderFoot), then a
    ``FeatureFlag`` override, then the tier-based default.  Returns False if
    blocked; caller should return an error response.

    When called from background tasks (no request context), pass *tenant_id*.
    """
    from cms.services import license as license_service

    if license_service.trial_blocked(feature):
        return False

    from flask_login import current_user
    from .models import FeatureFlag, Tenant as TenantModel

    if tenant_id is None:
        if not current_user.is_authenticated:
            return True
        tenant = current_user.tenant
        if not tenant:
            return True
        tenant_id = tenant.id
        tier = tenant.tier
    else:
        t = TenantModel.query.get(tenant_id)
        if not t:
            return True
        tier = t.tier

    # Check for super-admin override first
    override = FeatureFlag.query.filter_by(
        tenant_id=tenant_id, flag_name=feature
    ).first()
    if override is not None:
        return override.enabled

    # Fall back to tier default
    limits = _get_limits(tier)
    return bool(limits["features"].get(feature, False))


def check_resource_limit(
    model_class, foreign_key_attr, max_key: str, tenant=None, tier_name=None
):
    """Check if adding a resource would exceed the tier limit.
    Returns (ok: bool, current: int, maximum: int|None).
    If tenant/tier_name omitted, uses current_user's tenant.
    """
    if tenant is None and tier_name is None:
        from flask_login import current_user

        if not current_user.is_authenticated:
            return True, 0, None
        tenant = current_user.tenant
        tenant_id = current_user.tenant_id
    else:
        tenant_id = tenant.id if tenant else None

    if not tenant_id or not tenant:
        return True, 0, None

    actual_tier = tier_name or tenant.tier
    limits = _get_limits(actual_tier)
    maximum = limits.get(max_key)
    if maximum is None:
        return True, 0, None
    kwargs = {foreign_key_attr: tenant_id}
    current = model_class.query.filter_by(**kwargs).count()
    return current < maximum, current, maximum


def check_concurrent_spiderfoot_scans(
    tenant_id: str | None = None,
) -> tuple[bool, int, int | None]:
    """Check if the tenant has reached their concurrent SpiderFoot scan limit.

    Returns (ok: bool, current: int, maximum: int | None).
    """
    from flask_login import current_user

    if tenant_id is None:
        if not current_user.is_authenticated:
            return True, 0, None
        tenant = current_user.tenant
        if not tenant:
            return True, 0, None
        tenant_id = tenant.id
        tier = tenant.tier
    else:
        from .models import Tenant as TenantModel

        t = TenantModel.query.get(tenant_id)
        if not t:
            return True, 0, None
        tier = t.tier

    limits = _get_limits(tier)
    maximum = limits.get("max_concurrent_spiderfoot_scans")
    if maximum is None or maximum <= 0:
        return True, 0, maximum

    from .models import SpiderFootScan

    running = SpiderFootScan.query.filter(
        SpiderFootScan.tenant_id == tenant_id,
        SpiderFootScan.is_deleted == False,
        SpiderFootScan.status.in_(["pending", "running"]),
    ).count()
    return running < maximum, running, maximum


def check_storage_limit(
    tenant_id: str, tier_name: str | None = None, extra_bytes: int = 0
) -> tuple[bool, int, int | None]:
    """Check if adding *extra_bytes* would exceed the tenant's storage quota.

    Returns (ok, used_mb, max_mb).
    """
    from .models import db, Tenant, Document, Screenshot

    if tier_name is None:
        tenant = db.session.get(Tenant, tenant_id)
        if not tenant:
            return True, 0, None
        tier_name = tenant.tier

    limits = _get_limits(tier_name)
    max_mb = limits.get("max_storage_mb")
    if max_mb is None:
        return True, 0, None

    doc_bytes = (
        db.session.query(db.func.coalesce(db.func.sum(Document.file_size), 0))
        .filter(Document.tenant_id == tenant_id, Document.is_deleted == False)
        .scalar()
    )
    ss_bytes = (
        db.session.query(db.func.coalesce(db.func.sum(Screenshot.file_size), 0))
        .filter(Screenshot.tenant_id == tenant_id)
        .scalar()
    )
    used_mb = (doc_bytes + ss_bytes + extra_bytes) // (1024 * 1024)
    return used_mb < max_mb, used_mb, max_mb
