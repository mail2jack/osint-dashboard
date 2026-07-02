from datetime import datetime, timezone
import uuid

from ..models import db


class PlatformSetting(db.Model):
    """Global platform settings (SMTP, S3, Stripe keys, encryption keys)."""

    __tablename__ = "platform_settings"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(50), default="general")
    description = db.Column(db.String(500))
    is_encrypted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def get(cls, key: str, default: str | None = None) -> str | None:
        row = cls.query.filter_by(key=key).first()
        if not row:
            return default
        if row.is_encrypted and row.value:
            from ..encryption_utils import encryptor

            try:
                return encryptor.decrypt(row.value)
            except Exception:
                return default
        return row.value

    @classmethod
    def set(
        cls,
        key: str,
        value: str,
        category: str = "general",
        description: str = "",
        encrypt: bool = False,
    ) -> "PlatformSetting":
        from ..encryption_utils import encryptor

        row = cls.query.filter_by(key=key).first()
        if not row:
            row = cls(key=key)
        row.value = encryptor.encrypt(value) if encrypt else value
        row.category = category
        row.description = description
        row.is_encrypted = encrypt
        db.session.add(row)
        db.session.commit()
        return row
