"""Links customer portal identities to tenant-scoped client records."""

import uuid
from datetime import datetime, timezone

from . import db


class ClientPortalAccess(db.Model):
    """A portal identity's access to one client within one tenant."""

    __tablename__ = "client_portal_access"
    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "client_id", "user_id", name="uq_client_portal_access_user"
        ),
        db.Index("ix_client_portal_access_tenant_client", "tenant_id", "client_id"),
        db.Index("ix_client_portal_access_user_active", "user_id", "is_active"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=False, index=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False, default="portal_viewer")
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    invited_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    accepted_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tenant = db.relationship("Tenant", backref=db.backref("client_portal_access", lazy="dynamic"))
    client = db.relationship("Client", backref=db.backref("portal_access", lazy="dynamic"))
    user = db.relationship("User", foreign_keys=[user_id], backref="client_portal_links")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def revoke(self) -> None:
        self.is_active = False
        self.revoked_at = datetime.now(timezone.utc)
