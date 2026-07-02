from datetime import datetime, timezone
import uuid

from ..models import db


class DpaRecord(db.Model):
    """Data Processing Agreement register (Article 28 GDPR)."""

    __tablename__ = "dpa_records"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False, index=True)
    purpose = db.Column(db.String(500), nullable=False)
    data_categories = db.Column(db.String(500))
    country = db.Column(db.String(100))
    transfer_safeguard = db.Column(db.String(200))
    contract_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="active", index=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "data_categories": self.data_categories,
            "country": self.country,
            "transfer_safeguard": self.transfer_safeguard,
            "contract_date": self.contract_date.isoformat()
            if self.contract_date
            else None,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
