"""
Email sending utility for Iveras CMS.

Reads SMTP settings from the database (Setting model) and sends
emails via smtplib. Used for sending credentials to new users, etc.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def get_smtp_settings():
    """Read SMTP settings from database."""
    from .models import Setting
    return {
        'server': Setting.get('smtp_server'),
        'port': int(Setting.get('smtp_port') or 587),
        'username': Setting.get('smtp_username'),
        'password': Setting.get('smtp_password'),
        'from_email': Setting.get('smtp_from_email'),
        'from_name': Setting.get('smtp_from_name') or 'Iveras CMS',
    }


def is_smtp_configured():
    """Check if SMTP is configured in settings."""
    cfg = get_smtp_settings()
    return bool(cfg['server'] and cfg['from_email'])


def send_email(to_email, subject, body_html, body_text=None):
    """
    Send an email using configured SMTP settings.
    Returns True on success, False on failure.
    """
    cfg = get_smtp_settings()
    if not cfg['server'] or not cfg['from_email']:
        logger.warning("SMTP not configured — cannot send email")
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{cfg['from_name']} <{cfg['from_email']}>"
    msg['To'] = to_email

    if body_text:
        msg.attach(MIMEText(body_text, 'plain'))
    msg.attach(MIMEText(body_html, 'html'))

    try:
        if cfg['port'] == 465:
            server = smtplib.SMTP_SSL(cfg['server'], cfg['port'])
        else:
            server = smtplib.SMTP(cfg['server'], cfg['port'])
            server.starttls()

        if cfg['username'] and cfg['password']:
            server.login(cfg['username'], cfg['password'])

        server.sendmail(cfg['from_email'], [to_email], msg.as_string())
        server.quit()
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def send_new_user_credentials(email, username, password, full_name):
    """
    Send a new user their login credentials.
    """
    subject = "Your Iveras CMS account has been created"
    body_html = f"""
    <html><body style="font-family:sans-serif;padding:2rem;">
        <h2>Welcome to Iveras CMS</h2>
        <p>Hi {full_name},</p>
        <p>An account has been created for you:</p>
        <table style="background:#f5f5f5;padding:1rem;border-radius:8px;margin:1rem 0;">
            <tr><td style="padding:0.25rem 1rem 0.25rem 0;font-weight:600;">Username</td><td>{username}</td></tr>
            <tr><td style="padding:0.25rem 1rem 0.25rem 0;font-weight:600;">Password</td><td style="font-family:monospace;font-size:1.1rem;">{password}</td></tr>
        </table>
        <p><strong>Important:</strong> You will be required to set up two-factor authentication (2FA) on your first login.</p>
        <p>Login at the URL provided by your administrator.</p>
    </body></html>
    """
    body_text = f"""
Welcome to Iveras CMS

Hi {full_name},

An account has been created for you:
  Username: {username}
  Password: {password}

Important: You will be required to set up two-factor authentication (2FA) on your first login.
"""
    return send_email(email, subject, body_html, body_text)
