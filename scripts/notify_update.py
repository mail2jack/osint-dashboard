#!/usr/bin/env python3
"""
Send an email notification to all superadmins after a CLI update.

Usage:
    ./scripts/notify_update.py --dir /opt/osint-dashboard --status success --backup /path/to/backup.tar.gz.gpg
    ./scripts/notify_update.py --dir /opt/osint-dashboard --status failed

Called by scripts/update.sh after the update completes.
Exits with code 0 even on error (notification is best-effort).
"""

import argparse
import os
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Project directory")
    parser.add_argument("--status", required=True, choices=["success", "failed"])
    parser.add_argument("--backup", default="", help="Path to backup archive")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.dir)
    sys.path.insert(0, project_dir)

    os.environ.setdefault("CMS_CONFIG", "DevelopmentConfig")

    try:
        from app import app
        from cms.email_utils import send_email, is_smtp_configured
        from cms.models import User
    except ImportError as e:
        print(f"[notify] ⚠️  Kan Flask app niet laden ({e}) — email overgeslagen")
        return

    if not is_smtp_configured():
        print("[notify] ℹ️  SMTP niet geconfigureerd — email overgeslagen")
        return

    with app.app_context():
        admins = User.query.filter_by(is_super_admin=True).all()
        if not admins:
            print(
                "[notify] ℹ️  Geen superadmin gebruikers gevonden — email overgeslagen"
            )
            return

        status_icon = "✅" if args.status == "success" else "❌"
        status_text = "geslaagd" if args.status == "success" else "mislukt"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subject = f"{status_icon} Iveras CLI update {status_text} — {now_str}"

        backup_section = ""
        if args.backup and os.path.isfile(args.backup):
            backup_dir = os.path.dirname(args.backup)
            key_file = os.path.join(backup_dir, "backup-key.gpg")
            backup_section = f"""
            <tr><td style='padding:6px 12px;font-weight:600;'>Backup bestand</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{args.backup}</td></tr>
            <tr><td style='padding:6px 12px;font-weight:600;'>Backup key</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{key_file if os.path.isfile(key_file) else "niet gevonden"}</td></tr>
            """
        else:
            backup_section = "<tr><td style='padding:6px 12px;' colspan='2'>⚠️ Geen backup gevonden</td></tr>"

        body_html = f"""<html><body style="font-family:sans-serif;padding:2rem;max-width:700px;">
<h2>{status_icon} Iveras CLI update {status_text}</h2>
<p>Er is een update uitgevoerd via de command line (<code>scripts/update.sh</code>).</p>
<table style="width:100%;border-collapse:collapse;margin:1rem 0;">
<tr><td style="padding:6px 12px;font-weight:600;">Datum/tijd</td><td style="padding:6px 12px;">{now_str}</td></tr>
<tr><td style="padding:6px 12px;font-weight:600;">Status</td><td style="padding:6px 12px;">{status_text}</td></tr>
{backup_section}
</table>

<h3>Herstel bij problemen</h3>
<p style="background:#fff3cd;border:1px solid #ffc107;padding:1rem;border-radius:6px;">
SSH naar de server en gebruik:
</p>
<pre style="background:#f5f5f5;padding:1rem;border-radius:6px;font-size:0.85rem;">
# Bekijk beschikbare backups
./scripts/restore.sh --list

# Herstel de laatste backup (vraagt bevestiging)
./scripts/restore.sh

# Na herstel de service herstarten
sudo systemctl restart osint-dashboard
</pre>
<p style="color:#666;font-size:0.85rem;">Dit bericht is automatisch gegenereerd door Iveras CMS.</p>
</body></html>"""

        body_text = f"Iveras CLI update {status_text} — {now_str}\n\n"
        if args.backup:
            body_text += f"Backup: {args.backup}\n"
        body_text += "\nHerstel: SSH naar server en draai ./scripts/restore.sh\n"

        for admin in admins:
            try:
                send_email(admin.email, subject, body_html, body_text)
                print(f"[notify] ✅ Email naar {admin.email}")
            except Exception as e:
                print(f"[notify] ⚠️  Email naar {admin.email} mislukt: {e}")


if __name__ == "__main__":
    main()
