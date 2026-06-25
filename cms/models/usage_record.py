import uuid
from datetime import datetime, timezone

from . import db


class UsageRecord(db.Model):
    __tablename__ = "usage_records"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    record_date = db.Column(db.Date, nullable=False, index=True)
    metric_name = db.Column(db.String(50), nullable=False)
    metric_value = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id",
            "record_date",
            "metric_name",
            name="uq_usage_tenant_date_metric",
        ),
    )

    tenant = db.relationship(
        "Tenant", backref=db.backref("usage_records", lazy="dynamic")
    )

    @classmethod
    def upsert(
        cls, tenant_id: str, record_date, metric_name: str, metric_value: int
    ) -> "UsageRecord":
        existing = cls.query.filter_by(
            tenant_id=tenant_id,
            record_date=record_date,
            metric_name=metric_name,
        ).first()
        if existing:
            existing.metric_value = metric_value
            row = existing
        else:
            row = cls(
                tenant_id=tenant_id,
                record_date=record_date,
                metric_name=metric_name,
                metric_value=metric_value,
            )
            db.session.add(row)
        db.session.flush()
        return row
