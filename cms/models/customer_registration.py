import uuid
from datetime import datetime, timezone

from . import db


class CustomerRegistrationRequest(db.Model):
    """Pre-approval request for a new customer organization.

    A request may be assigned to an existing investigation tenant before
    approval. Until then it remains an unassigned platform-level intake.
    """

    __tablename__ = "customer_registration_requests"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=True, index=True)
    registration_type = db.Column(db.String(30), nullable=False, default="one_off", index=True)
    organization_name = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(50))
    message = db.Column(db.Text)
    staff_response = db.Column(db.Text)
    internal_note = db.Column(db.Text)
    follow_up_at = db.Column(db.DateTime)
    handled_at = db.Column(db.DateTime)
    status = db.Column(db.String(30), nullable=False, default="submitted", index=True)
    reviewed_by_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(500))
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    tenant = db.relationship("Tenant", foreign_keys=[tenant_id])
