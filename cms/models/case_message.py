"""Tenant-scoped chat messages between a customer and the case handler."""

from datetime import datetime, timezone
import uuid

from . import db


class CaseMessage(db.Model):
    __tablename__ = "case_messages"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), nullable=False, index=True)
    sender_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    sender_role = db.Column(db.String(20), nullable=False)  # customer / staff
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    tenant = db.relationship("Tenant")
    case = db.relationship("Case", backref=db.backref("messages", order_by="CaseMessage.created_at.asc()"))
    sender = db.relationship("User")
