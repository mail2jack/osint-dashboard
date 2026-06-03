"""One-time migration: copy .env API keys to Settings table.
Run: /usr/local/bin/python3 scripts/migrate_env_to_settings.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from cms.models import db, Setting

MAPPINGS = {
    "OVERHEID_API_KEY": "overheid_api_key",
    "TWOCHAT_API_KEY": "twochat_api_key",
    "TWOCHAT_WHATSAPP_NUMBER": "twochat_whatsapp_number",
}


def migrate():
    with app.app_context():
        db.create_all()
        migrated = 0
        for env_key, setting_key in MAPPINGS.items():
            val = os.environ.get(env_key, "")
            if val:
                existing = Setting.get(setting_key, "")
                if not existing:
                    Setting.set(setting_key, val)
                    print(f"  ✅ {setting_key} ← ${env_key} (value hidden)")
                    migrated += 1
                else:
                    print(f"  ⏭️  {setting_key} already set in DB, skipping")
            else:
                print(f"  ⏭️  ${env_key} not set in .env, skipping")
        if migrated:
            db.session.commit()
            print(f"\nMigrated {migrated} key(s). You can now remove them from .env.")
        else:
            print("\nNothing to migrate.")


if __name__ == "__main__":
    print("Migrating API keys from .env to Settings table...")
    migrate()
