from datetime import datetime, timezone
import uuid

from ..models import db


class PortalRequest(db.Model):
    """Tenant-scoped intake request submitted through the customer portal."""

    __tablename__ = "portal_requests"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    submitted_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    assigned_to_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True, index=True)
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), nullable=True, index=True)
    duplicate_of_id = db.Column(
        db.String(36), db.ForeignKey("portal_requests.id"), nullable=True, index=True
    )
    request_type = db.Column(db.String(50), nullable=False)
    question = db.Column(db.Text, nullable=False)
    context = db.Column(db.Text)
    deadline = db.Column(db.String(200))
    status = db.Column(db.String(30), nullable=False, default="submitted", index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    tenant = db.relationship("Tenant", backref=db.backref("portal_requests", lazy="dynamic"))
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    client = db.relationship("Client", foreign_keys=[client_id])
    case = db.relationship("Case", foreign_keys=[case_id])
    duplicate_of = db.relationship(
        "PortalRequest",
        remote_side=[id],
        foreign_keys=[duplicate_of_id],
        backref=db.backref("possible_duplicates", lazy="dynamic"),
    )
