#!/usr/bin/env python3
"""Send a concise backup-completion email to all superadmins.

This is deliberately best-effort: a mail or SMTP failure must never alter the
exit status of the backup that created the result.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import datetime
from pathlib import Path


def _human_size(path: Path) -> str:
    if not path.is_file():
        return "niet beschikbaar"
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return "niet beschikbaar"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Project directory")
    parser.add_argument("--status", required=True, choices=("success", "failed"))
    parser.add_argument("--archive", default="", help="Encrypted archive path")
    parser.add_argument("--errors", type=int, default=0)
    parser.add_argument("--warnings", type=int, default=0)
    args = parser.parse_args()

    project_dir = os.path.abspath(args.dir)
    sys.path.insert(0, project_dir)
    os.environ.setdefault("CMS_CONFIG", "DevelopmentConfig")

    try:
        from app import app
        from cms.email_utils import is_smtp_configured, send_email
        from cms.models import User, db
        from cms.tenant_context import set_tenant_context
    except Exception as exc:  # Notification failures never affect the backup.
        print(f"[backup-notify] mail skipped: app unavailable ({exc})")
        return 0

    archive = Path(args.archive)
    archive_id = archive.name if args.archive else "geen archive"
    status_icon = "✅" if args.status == "success" else "❌"
    status_text = "geslaagd" if args.status == "success" else "mislukt"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"{status_icon} Iveras backup {status_text} — {now}"

    rows = (
        ("Tijdstip", now),
        ("Status", status_text),
        ("Backup-ID", archive_id),
        ("Archiefgrootte", _human_size(archive)),
        ("Fouten", str(args.errors)),
        ("Waarschuwingen", str(args.warnings)),
        ("Integriteitscontrole", "uitgevoerd" if archive.is_file() else "niet voltooid"),
    )
    table = "".join(
        "<tr><td style='padding:6px 12px;font-weight:600;'>"
        f"{html.escape(label)}</td><td style='padding:6px 12px;'>"
        f"{html.escape(value)}</td></tr>"
        for label, value in rows
    )
    body_html = f"""<html><body style='font-family:sans-serif;padding:2rem;max-width:640px;'>
<h2>{status_icon} Iveras backup {status_text}</h2>
<p>Dit is een automatische melding na afronding van de productiebackup.</p>
<table style='border-collapse:collapse;margin:1rem 0;'>{table}</table>
<p style='color:#666;font-size:.9rem;'>Een afzonderlijke DR-herstelverificatie rapporteert apart.</p>
</body></html>"""
    body_text = "Iveras backup " + status_text + "\n\n" + "\n".join(
        f"{label}: {value}" for label, value in rows
    )

    with app.app_context():
        set_tenant_context(db, None, bypass_rls=True)
        if not is_smtp_configured():
            print("[backup-notify] mail skipped: SMTP not configured")
            return 0
        recipients = {user.email for user in User.query.filter_by(is_super_admin=True)}
        for email in recipients:
            try:
                send_email(email, subject, body_html, body_text)
                print(f"[backup-notify] email sent to {email}")
            except Exception as exc:
                print(f"[backup-notify] email to {email} failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
