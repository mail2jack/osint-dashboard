#!/usr/bin/env python3
"""Reset all user passwords to Test1234! (dev/test only)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("FLASK_ENV", "production")

from app import app
from cms.models import db, User

with app.app_context():
    users = User.query.all()
    for u in users:
        u.set_password("Test1234!")
    db.session.commit()
    print(f"✅ Passwords reset to Test1234! for {len(users)} user(s):")
    for u in users:
        print(f"   - {u.username} ({u.full_name or 'no name'})")
