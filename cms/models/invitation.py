import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import db


class Invitation(db.Model):
    __tablename__ = "invitations"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    email = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False, default="investigator")
    invited_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    token = db.Column(db.String(128), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    tenant = db.relationship("Tenant", backref="invitations")
    invited_by = db.relationship("User", backref="invitations_sent")

    @classmethod
    def create_invitation(
        cls, tenant_id: str, email: str, role: str, invited_by_id: str
    ) -> "Invitation":
        token = uuid.uuid4().hex + uuid.uuid4().hex
        expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
        inv = cls(
            tenant_id=tenant_id,
            email=email.strip().lower(),
            role=role,
            invited_by_id=invited_by_id,
            token=token,
            expires_at=expires_at,
        )
        db.session.add(inv)
        db.session.flush()
        return inv

    @classmethod
    def find_valid(cls, token: str) -> Optional["Invitation"]:
        return cls.query.filter(
            cls.token == token,
            cls.accepted_at.is_(None),
            cls.expires_at > datetime.now(timezone.utc),
        ).first()

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def accept(self) -> None:
        self.accepted_at = datetime.now(timezone.utc)
