"""Forensic analysis reports for customer-registration email evidence."""

from datetime import datetime, timezone
import uuid

from . import db


class CustomerRegistrationMailAnalysis(db.Model):
    __tablename__ = "customer_registration_mail_analyses"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    registration_id = db.Column(
        db.String(36), db.ForeignKey("customer_registration_requests.id"), nullable=False, index=True
    )
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=True, index=True)
    created_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    verdict = db.Column(db.String(40), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    report = db.Column(db.JSON, nullable=False)
    raw_message_encrypted = db.Column(db.Text, nullable=False)
    message_sha256 = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )

    registration = db.relationship(
        "CustomerRegistrationRequest",
        backref=db.backref("mail_analyses", order_by="CustomerRegistrationMailAnalysis.created_at.desc()"),
    )
    created_by = db.relationship("User")
    tenant = db.relationship("Tenant")
