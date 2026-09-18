"""Persisted, tenant-isolated queue entries for future finding captures.

The web process only creates these records.  A separately deployed worker will
claim and execute them in a later FEAT-1 slice; this model deliberately has no
Playwright dependency.
"""

from datetime import datetime, timezone
import uuid

from ..models import SafeJSON, db


class FindingCaptureJob(db.Model):
    __tablename__ = "finding_capture_jobs"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_finding_capture_job_status",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), nullable=False)
    finding_id = db.Column(db.String(36), db.ForeignKey("findings.id"), nullable=False)
    requested_by_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False
    )
    target_url = db.Column(db.String(2000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    screenshot_id = db.Column(
        db.String(36), db.ForeignKey("finding_screenshots.id"), nullable=True
    )
    error = db.Column(db.String(300), nullable=True)
    request_metadata = db.Column(SafeJSON, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
