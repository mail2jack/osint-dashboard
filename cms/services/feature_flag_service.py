"""Audited, transaction-owned writes for tenant feature-flag overrides."""

from dataclasses import dataclass

from cms.feature_flag_policy import tier_default, validate_flag_name
from cms.models import AuditLog, FeatureFlag, Tenant, User, db

SYSTEM_SOURCES = frozenset({"seed_testdata", "rollout", "maintenance"})


@dataclass(frozen=True)
class FeatureFlagChange:
    operation: str
    override: FeatureFlag | None
    changed: bool


def _snapshot(value: bool | None, effective: bool, default: bool) -> dict:
    return {
        "override": value,
        "effective": effective,
        "tier_default": default,
    }


def _set_feature_flag(
    *,
    tenant: Tenant,
    flag_name: str,
    enabled: bool,
    actor_id: str | None,
    source: str,
) -> FeatureFlagChange:
    """Set the desired effective value without committing the transaction.

    A value equal to the tier default removes the override. Repeating the same
    request is a true no-op: no timestamp mutation and no audit entry.
    """
    validate_flag_name(flag_name)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    default = tier_default(flag_name, tenant.tier)
    override = FeatureFlag.query.filter_by(
        tenant_id=tenant.id, flag_name=flag_name
    ).first()
    old_override = override.enabled if override else None
    old_effective = old_override if old_override is not None else default

    desired_override = None if enabled == default else enabled
    if old_override == desired_override:
        return FeatureFlagChange("noop", override, False)

    if desired_override is None:
        operation = "delete"
        db.session.delete(override)
        resulting_override = None
    elif override is None:
        operation = "create"
        override = FeatureFlag(
            tenant_id=tenant.id,
            flag_name=flag_name,
            enabled=desired_override,
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )
        db.session.add(override)
        db.session.flush()
        resulting_override = override
    else:
        operation = "update"
        override.enabled = desired_override
        override.updated_by_id = actor_id
        resulting_override = override

    AuditLog.log(
        user_id=actor_id,
        action=operation,
        entity_type="feature_flag",
        entity_id=override.id,
        old_values=_snapshot(old_override, old_effective, default),
        new_values=_snapshot(desired_override, enabled, default),
        tenant_id=tenant.id,
        description=f"Feature flag {flag_name} changed via {source}",
    )
    return FeatureFlagChange(operation, resulting_override, True)


def set_feature_flag_by_superadmin(
    *, tenant: Tenant, flag_name: str, enabled: bool, actor: User
) -> FeatureFlagChange:
    """Human write entrypoint; callers cannot bypass the super-admin check."""
    if not actor.is_super_admin:
        raise PermissionError("Feature flags require a super-admin actor")
    return _set_feature_flag(
        tenant=tenant,
        flag_name=flag_name,
        enabled=enabled,
        actor_id=actor.id,
        source="super_admin_ui",
    )


def set_feature_flag_by_system(
    *, tenant: Tenant, flag_name: str, enabled: bool, source: str
) -> FeatureFlagChange:
    """Non-HTTP system entrypoint with a closed source allowlist."""
    if source not in SYSTEM_SOURCES:
        raise ValueError("Unknown system feature-flag source")
    return _set_feature_flag(
        tenant=tenant,
        flag_name=flag_name,
        enabled=enabled,
        actor_id=None,
        source=source,
    )
