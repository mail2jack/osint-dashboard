from datetime import datetime, timedelta, timezone
import uuid

from ..models import db


class BreachRecord(db.Model):
    """Breach notification record for GDPR Articles 33-34."""

    __tablename__ = "breach_records"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    detected_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    breach_type = db.Column(db.String(100))
    description = db.Column(db.Text, nullable=False)
    data_affected = db.Column(db.Text)
    affected_count = db.Column(db.Integer, nullable=True)
    risk_level = db.Column(
        db.String(20), default="unknown"
    )  # low, medium, high, critical
    status = db.Column(
        db.String(20), default="open", index=True
    )  # open, investigating, mitigated, closed

    # Art. 33 — notification to supervisory authority (72h)
    authority_notified = db.Column(db.Boolean, default=False)
    authority_notified_at = db.Column(db.DateTime, nullable=True)
    authority_notes = db.Column(db.Text)

    # Art. 34 — communication to data subjects
    subjects_notified = db.Column(db.Boolean, default=False)
    subjects_notified_at = db.Column(db.DateTime, nullable=True)
    subject_communication = db.Column(db.Text)

    remedial_actions = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def deadline_72h(self) -> datetime:
        return self.detected_at + timedelta(hours=72)

    @property
    def hours_remaining(self) -> float:
        remaining = (self.deadline_72h - datetime.now(timezone.utc)).total_seconds()
        return max(0, remaining / 3600)

    @property
    def is_overdue(self) -> bool:
        if self.authority_notified:
            return False
        return datetime.now(timezone.utc) > self.deadline_72h

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "breach_type": self.breach_type,
            "description": self.description,
            "data_affected": self.data_affected,
            "affected_count": self.affected_count,
            "risk_level": self.risk_level,
            "status": self.status,
            "authority_notified": self.authority_notified,
            "authority_notified_at": self.authority_notified_at.isoformat()
            if self.authority_notified_at
            else None,
            "authority_notes": self.authority_notes,
            "subjects_notified": self.subjects_notified,
            "subjects_notified_at": self.subjects_notified_at.isoformat()
            if self.subjects_notified_at
            else None,
            "subject_communication": self.subject_communication,
            "remedial_actions": self.remedial_actions,
            "deadline_72h": self.deadline_72h.isoformat(),
            "hours_remaining": round(self.hours_remaining, 1),
            "is_overdue": self.is_overdue,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
