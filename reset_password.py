"""Reset password + disable TOTP for ivan.versteegh@protonmail.com.

Run on VPS:
  cd ~/osint-dashboard
  python3 reset_password.py
"""
import secrets
import string

from cms.models import User, db

email = "ivan.versteegh@protonmail.com"
nieuw = "".join(
    secrets.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(20)
)

user = User.query.filter_by(email=email).first()
if not user:
    print(f"Geen gebruiker gevonden met email {email}")
    raise SystemExit(1)

user.set_password(nieuw)
user.totp_enabled = False
user.totp_secret = None
db.session.commit()

print(f"Wachtwoord gereset: {nieuw}")
print("TOTP uitgeschakeld.")
