"""
Email sending utility for Iveras CMS.

Reads SMTP settings from the database (Setting model) and sends
emails via smtplib. Used for sending credentials to new users, etc.
"""

import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def get_smtp_settings() -> dict:
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


def is_smtp_configured() -> bool:
    """Check if SMTP is configured in settings."""
    cfg = get_smtp_settings()
    return bool(cfg['server'] and cfg['from_email'])


def send_email(to_email, subject, body_html, body_text=None) -> bool:
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
        ctx = ssl.create_default_context()
        if cfg['port'] == 465:
            server = smtplib.SMTP_SSL(cfg['server'], cfg['port'], context=ctx)
        else:
            server = smtplib.SMTP(cfg['server'], cfg['port'])
            server.starttls(context=ctx)

        if cfg['username'] and cfg['password']:
            server.login(cfg['username'], cfg['password'])

        server.sendmail(cfg['from_email'], [to_email], msg.as_string())
        server.quit()
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def send_new_user_credentials(email, username, password, full_name) -> bool:
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

Important: You will be required to set up two-factor authentication (2FA) on your first login.
"""
    return send_email(email, subject, body_html, body_text)


def send_password_reset_email(email, username, full_name, reset_url) -> bool:
    """
    Send a password reset link to the user.
    """
    subject = "Set your Iveras CMS password"
    body_html = f"""
    <html><body style="font-family:sans-serif;padding:2rem;">
        <h2>Set Your Password</h2>
        <p>Hi {full_name},</p>
        <p>Click the link below to set your password for <strong>{username}</strong>:</p>
        <p style="text-align:center;margin:2rem 0;">
            <a href="{reset_url}" style="background:#1a73e8;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;font-size:1.1rem;">
                Set Password
            </a>
        </p>
        <p>Or copy this URL into your browser:</p>
        <p style="font-family:monospace;background:#f5f5f5;padding:0.5rem;border-radius:4px;">{reset_url}</p>
        <p><strong>Note:</strong> This link expires in 48 hours.</p>
        <p>If you did not request this, please ignore this email.</p>
    </body></html>
    """
    body_text = f"""
Set Your Iveras CMS Password

Hi {full_name},

Use the link below to set your password for {username}:
{reset_url}

Note: This link expires in 48 hours.
"""
    return send_email(email, subject, body_html, body_text)
