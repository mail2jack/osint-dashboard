from datetime import datetime, timezone

from ..models import db, SafeJSON


class BackgroundTask(db.Model):
    """Persisted background task for fire-and-forget execution."""

    __tablename__ = "background_tasks"

    id = db.Column(db.String(64), primary_key=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    result = db.Column(SafeJSON)
    error = db.Column(db.Text)
    task_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "task_name": self.task_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
