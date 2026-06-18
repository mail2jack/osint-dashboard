import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from ..models import db, SafeJSON

logger = logging.getLogger(__name__)


class Setting(db.Model):
    """
    Application settings stored in database.
    Allows runtime configuration without code changes.
    Supports encrypted storage for sensitive values (API keys, credentials).
    """

    __tablename__ = "settings"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)
    category = db.Column(db.String(50), default="general", index=True)
    description = db.Column(db.String(500))
    value_type = db.Column(db.String(20), default="text")
    options = db.Column(SafeJSON)
    is_encrypted = db.Column(db.Boolean, default=False)
    is_sensitive = db.Column(db.Boolean, default=False)
    display_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)

    def get_masked_value(self) -> str:
        if not self.is_sensitive or not self.value:
            return self.value or ""
        if len(self.value) <= 4:
            return "****"
        return self.value[:2] + "*" * (len(self.value) - 4) + self.value[-2:]

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        setting = Setting.query.filter_by(key=key, is_active=True).first()
        if setting is None:
            return default
        if setting.is_encrypted:
            from ..encryption_utils import encryptor

            try:
                return encryptor.decrypt(setting.value)
            except Exception:
                return default
        if setting.value_type == "json" and setting.value:
            try:
                return json.loads(setting.value)
            except (json.JSONDecodeError, TypeError):
                pass
        return setting.value

    @staticmethod
    def set(
        key: str,
        value: Any,
        description: str = None,
        category: str = "general",
        encrypt: bool = False,
    ) -> bool:
        setting = Setting.query.filter_by(key=key).first()
        if setting is None:
            setting = Setting(key=key, category=category, description=description)
            db.session.add(setting)
        setting.is_active = True
        if encrypt and value:
            from ..encryption_utils import encryptor

            setting.value = encryptor.encrypt(str(value))
            setting.is_encrypted = True
            setting.value_type = "password"
        else:
            if isinstance(value, (list, dict)):
                setting.value = json.dumps(value, default=str)
                setting.value_type = "json"
            else:
                setting.value = str(value) if value is not None else None
                setting.is_encrypted = False
        if description:
            setting.description = description
        if category:
            setting.category = category
        setting.updated_at = datetime.now(timezone.utc)
        try:
            db.session.commit()
            try:
                from ..setting_cache import invalidate_setting

                invalidate_setting(key)
            except Exception:
                pass
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save setting {key}: {e}")
            return False

    def to_dict(self, include_value: bool = True) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value if include_value else None,
            "masked_value": self.get_masked_value(),
            "category": self.category,
            "description": self.description,
            "value_type": self.value_type,
            "options": self.options,
            "is_sensitive": self.is_sensitive,
            "display_order": self.display_order,
            "is_active": self.is_active,
        }
