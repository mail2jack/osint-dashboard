"""Customer-visible follow-up messages attached to a portal request."""

from datetime import datetime, timezone
import uuid

from . import db


class PortalRequestMessage(db.Model):
    __tablename__ = "portal_request_messages"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    request_id = db.Column(
        db.String(36), db.ForeignKey("portal_requests.id"), nullable=False, index=True
    )
    author_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )

    tenant = db.relationship("Tenant")
    request = db.relationship(
        "PortalRequest",
        backref=db.backref("messages", order_by="PortalRequestMessage.created_at.asc()"),
    )
    author = db.relationship("User")
