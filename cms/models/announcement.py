"""System-wide announcement model for super admin broadcasts."""

from datetime import datetime, timezone

from . import db


class Announcement(db.Model):
    """System-wide announcement shown to all users on login."""

    __tablename__ = "announcements"

    id = db.Column(
        db.String(36), primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    severity = db.Column(
        db.String(20), default="info"
    )  # info, warning, error, critical
    starts_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship(
        "User", backref=db.backref("announcements_created", lazy="dynamic")
    )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "severity": self.severity,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "created_by_id": self.created_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AnnouncementAck(db.Model):
    """Tracks which users have acknowledged which announcements."""

    __tablename__ = "announcement_acks"

    id = db.Column(
        db.String(36), primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    announcement_id = db.Column(
        db.String(36), db.ForeignKey("announcements.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    acknowledged_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    announcement = db.relationship(
        "Announcement", backref=db.backref("acks", lazy="dynamic")
    )
    user = db.relationship(
        "User", backref=db.backref("announcement_acks", lazy="dynamic")
    )

    __table_args__ = (
        db.UniqueConstraint("announcement_id", "user_id", name="uq_announcement_user"),
    )
