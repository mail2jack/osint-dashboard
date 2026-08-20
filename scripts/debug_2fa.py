#!/usr/bin/env python3
"""Quick diagnostic: trace the full 2FA login flow."""

import requests
import re
import pyotp

BASE = "https://joost.iveras.com"
EMAIL = "ivan.versteegh@protonmail.com"
PASSWORD = "Test1234!"
SECRET = "HNZEHJLYITA7Y7BZB3M24O7ZJNROYPQZ"

s = requests.Session()

# 1. GET login
r = s.get(f"{BASE}/auth/login")
print(f"1. Login GET: {r.status_code}")
print(f"   Cookies: {dict(s.cookies)}")
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text).group(1)

# 2. POST login
r = s.post(
    f"{BASE}/auth/login",
    data={"email": EMAIL, "password": PASSWORD, "csrf_token": csrf},
    headers={"Origin": BASE, "Referer": f"{BASE}/auth/login"},
    allow_redirects=False,
)
print(f"2. Login POST: {r.status_code} -> {r.headers.get('Location', 'NO REDIRECT')}")
print(f"   Set-Cookie: {r.headers.get('Set-Cookie', 'NONE')[:120]}")
print(f"   Cookies: {dict(s.cookies)}")

# 3. GET 2fa
r = s.get(f"{BASE}/auth/2fa/verify")
print(f"3. 2FA GET: {r.status_code}")
print(f"   Cookies: {dict(s.cookies)}")
csrf2 = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text).group(1)
code = pyotp.TOTP(SECRET).now()
print(f"   TOTP code: {code}")

# 4. POST 2fa
r = s.post(
    f"{BASE}/auth/2fa/verify",
    data={"code": code, "csrf_token": csrf2},
    headers={"Origin": BASE, "Referer": f"{BASE}/auth/2fa/verify"},
    allow_redirects=False,
)
print(f"4. 2FA POST: {r.status_code} -> {r.headers.get('Location', 'NO REDIRECT')}")
lines = [
    ln.strip()
    for ln in r.text.split("\n")
    if any(w in ln.lower() for w in ["invalid", "too many", "danger", "flash"])
]
print(f"   Errors: {lines[:3]}")
print(f"   Cookies: {dict(s.cookies)}")

# 5. Dashboard
r = s.get(f"{BASE}/cms/")
print(f"5. Dashboard: {r.status_code}")
