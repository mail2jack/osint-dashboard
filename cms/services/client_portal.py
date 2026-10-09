"""Authorization helpers for client-scoped customer portal access."""

from sqlalchemy import select

from cms.models import ClientPortalAccess, db


PORTAL_ROLES = {"portal_admin", "portal_member", "portal_viewer"}


def is_portal_user(user) -> bool:
    return bool(user and user.is_authenticated and user.role in PORTAL_ROLES)


def portal_client_ids(user) -> list[str] | None:
    """Return allowed client IDs; None means legacy portal-only tenant scope."""
    if not is_portal_user(user):
        return None
    tenant = getattr(user, "tenant", None)
    if tenant and tenant.workspace_type == "portal_only":
        return None
    return list(
        db.session.scalars(
            select(ClientPortalAccess.client_id).where(
                ClientPortalAccess.tenant_id == user.tenant_id,
                ClientPortalAccess.user_id == user.id,
                ClientPortalAccess.is_active.is_(True),
            )
        )
    )


def portal_can_access_client(user, client_id: str | None) -> bool:
    allowed = portal_client_ids(user)
    return allowed is None or bool(client_id and client_id in allowed)
