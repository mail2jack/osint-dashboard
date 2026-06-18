"""One-time migration: convert Subject.social_media_ids (JSON) → SocialAccount rows.

Usage: python3 scripts/migrate_social_ids.py
Run with DATABASE_URL set in .env or environment.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app
from cms.models import db, Subject, SocialAccount


def migrate():
    with flask_app.app_context():
        all_subjects = Subject.query.filter(
            Subject.social_media_ids.isnot(None),
        ).all()
        subjects = []
        for s in all_subjects:
            val = s.social_media_ids
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    val = {}
            if val and isinstance(val, dict) and len(val) > 0:
                subjects.append(s)

        total_accounts = 0
        total_subjects = 0

        for subject in subjects:
            ids = subject.social_media_ids
            if isinstance(ids, str):
                try:
                    ids = json.loads(ids)
                except (json.JSONDecodeError, TypeError):
                    ids = {}
            if not ids or not isinstance(ids, dict):
                continue

            # Check existing accounts for this subject
            existing = {
                (a.platform, a.username, a.account_id or "")
                for a in SocialAccount.query.filter_by(subject_id=subject.id).all()
            }

            created = 0
            for platform, data in ids.items():
                if isinstance(data, dict):
                    uid = data.get("id") or ""
                    username = data.get("username") or ""
                    url = data.get("url") or ""
                else:
                    uid = str(data) if data else ""
                    username = uid
                    url = ""

                if not uid and not username:
                    continue

                display_name = username or uid
                key = (platform, display_name, uid)
                if key in existing:
                    continue

                account = SocialAccount(
                    tenant_id=subject.tenant_id,
                    subject_id=subject.id,
                    platform=platform,
                    username=display_name,
                    url=url,
                    account_id=uid,
                )
                db.session.add(account)
                created += 1

            if created:
                total_accounts += created
                total_subjects += 1
                db.session.commit()
                print(f"  {subject.name}: {created} account(s) created")

        print(
            f"\nDone. {total_accounts} accounts created across {total_subjects} subjects."
        )


if __name__ == "__main__":
    migrate()
