"""
SaaS tier limits — max users, max cases, and feature gating per plan.
"""

TIERS = {
    "free": {
        "max_users": 2,
        "max_cases": 5,
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


def _get_limits(tier_name: str) -> dict:
    return TIERS.get(tier_name, TIERS["free"])


def check_feature(feature: str) -> bool:
    """Check if the current tenant's tier allows a feature.
    Returns False if blocked; caller should return error response."""
    from flask_login import current_user

    if not current_user.is_authenticated:
        return True
    tenant = current_user.tenant
    if not tenant:
        return True
    limits = _get_limits(tenant.tier)
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
