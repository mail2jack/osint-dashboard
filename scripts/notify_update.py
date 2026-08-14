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
import platform
import socket
import sys
from datetime import datetime

import requests


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
        from cms.models import db, User
        from cms.tenant_context import set_tenant_context
    except ImportError as e:
        print(f"[notify] ⚠️  Cannot load Flask app ({e}) — email skipped")
        return

    with app.app_context():
        set_tenant_context(db, None, bypass_rls=True)
        if not is_smtp_configured():
            print("[notify] ℹ️  SMTP not configured — email skipped")
            return

        admins = User.query.filter_by(is_super_admin=True).all()

        status_icon = "✅" if args.status == "success" else "❌"
        status_text = "succeeded" if args.status == "success" else "failed"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subject = f"{status_icon} Iveras CLI update {status_text} — {now_str}"

        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = "onbekend"
        public_ip = "onbekend"
        try:
            public_ip = (
                requests.get(
                    "https://api.ipify.org",
                    timeout=5,
                    proxies={"http": None, "https": None},
                ).text.strip()
                or "onbekend"
            )
        except Exception:
            pass
        local_ips = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ip = info[4][0]
                if ip not in local_ips:
                    local_ips.append(ip)
        except Exception:
            pass
        if not local_ips:
            local_ips = ["127.0.0.1"]
        try:
            os_label = platform.platform()
        except Exception:
            os_label = "onbekend"
        try:
            kernel = platform.release()
        except Exception:
            kernel = "onbekend"

        backup_section = ""
        if args.backup and os.path.isfile(args.backup):
            backup_dir = os.path.dirname(args.backup)
            key_file = os.path.join(backup_dir, "backup-key.gpg")
            backup_section = f"""
            <tr><td style='padding:6px 12px;font-weight:600;'>Backup file</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{args.backup}</td></tr>
            <tr><td style='padding:6px 12px;font-weight:600;'>Backup key</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{key_file if os.path.isfile(key_file) else "not found"}</td></tr>
            """
        else:
            backup_section = "<tr><td style='padding:6px 12px;' colspan='2'>⚠️ No backup found</td></tr>"

        sysinfo_section = f"""
        <tr><td style='padding:6px 12px;font-weight:600;'>Hostname</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{hostname}</td></tr>
        <tr><td style='padding:6px 12px;font-weight:600;'>Public IP</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{public_ip}</td></tr>
        <tr><td style='padding:6px 12px;font-weight:600;'>Local IP</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{", ".join(local_ips)}</td></tr>
        <tr><td style='padding:6px 12px;font-weight:600;'>OS</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{os_label}</td></tr>
        <tr><td style='padding:6px 12px;font-weight:600;'>Kernel</td><td style='padding:6px 12px;font-family:monospace;font-size:0.85rem;'>{kernel}</td></tr>
        """

        body_html = f"""<html><body style="font-family:sans-serif;padding:2rem;max-width:700px;">
<h2>{status_icon} Iveras CLI update {status_text}</h2>
<p>An update was performed via the command line (<code>scripts/update.sh</code>).</p>
<table style="width:100%;border-collapse:collapse;margin:1rem 0;">
<tr><td style="padding:6px 12px;font-weight:600;">Date/time</td><td style="padding:6px 12px;">{now_str}</td></tr>
<tr><td style="padding:6px 12px;font-weight:600;">Status</td><td style="padding:6px 12px;">{status_text}</td></tr>
{sysinfo_section}
{backup_section}
</table>

<h3>Recovery</h3>
<p style="background:#fff3cd;border:1px solid #ffc107;padding:1rem;border-radius:6px;">
SSH into the server and use:
</p>
<pre style="background:#f5f5f5;padding:1rem;border-radius:6px;font-size:0.85rem;">
# View available backups
./scripts/restore.sh --list

# Restore the latest backup (prompts for confirmation)
./scripts/restore.sh

# Restart the service after restore
sudo systemctl restart osint-dashboard
</pre>
<p style="color:#666;font-size:0.85rem;">This message was automatically generated by Iveras CMS.</p>
</body></html>"""

        body_text = f"Iveras CLI update {status_text} — {now_str}\n\n"
        body_text += (
            f"Hostname: {hostname}\nPublic IP: {public_ip}\n"
            f"Local IP: {', '.join(local_ips)}\nOS: {os_label}\nKernel: {kernel}\n"
        )
        if args.backup:
            body_text += f"Backup: {args.backup}\n"
        body_text += "\nRecovery: SSH into the server and run ./scripts/restore.sh\n"

        recipients = {a.email for a in admins}
        recipients.add("server_update@iveras.com")
        for email in recipients:
            try:
                send_email(email, subject, body_html, body_text)
                print(f"[notify] ✅ Email sent to {email}")
            except Exception as e:
                print(f"[notify] ⚠️  Email to {email} failed: {e}")


if __name__ == "__main__":
    main()
