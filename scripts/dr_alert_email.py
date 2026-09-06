#!/usr/bin/env python3
"""
Send the DR backup-verification alert email through the app SMTP settings.

Unlike the local `mail`/postfix path (whose sender domain is rejected by the
far-end MX), this uses the same cms.email_utils SMTP relay that
scripts/notify_update.py relies on for update notifications.

Usage:
    ./scripts/dr_alert_email.py --dir /opt/osint-dashboard --subject "..."
        --message "..." --to first@example.com [--to second@example.com ...]

Exits with code 0 even on error (notification is best-effort).
"""

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Project directory")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--to", required=True, action="append", help="Recipient")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.dir)
    sys.path.insert(0, project_dir)

    os.environ.setdefault("CMS_CONFIG", "DevelopmentConfig")

    try:
        from app import app
        from cms.email_utils import send_email, is_smtp_configured
        from cms.models import db
        from cms.tenant_context import set_tenant_context
    except ImportError as e:
        print(f"[dr-alert-email] ⚠️  Cannot load Flask app ({e}) — email skipped")
        return 0

    with app.app_context():
        set_tenant_context(db, None, bypass_rls=True)
        if not is_smtp_configured():
            print("[dr-alert-email] ℹ️  SMTP not configured — email skipped")
            return 0

        body_text = args.message
        body_html = f"<pre>{args.message}</pre>"
        for email in args.to:
            try:
                send_email(email, args.subject, body_html, body_text)
                print(f"[dr-alert-email] ✅ Email sent to {email}")
            except Exception as e:
                print(f"[dr-alert-email] ⚠️  Email to {email} failed: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())