#!/usr/bin/env python3
"""
Server diagnostics & auto-repair script for OSINT Dashboard.
Run as root:  sudo python3 scripts/doctor.py
Run dry:     sudo python3 scripts/doctor.py --dry-run

Checks:
  1. Home directory /home/osint exists and owned by osint
  2. osint user exists
  3. .spiderfoot directory exists and is owned by osint
  4. SpiderFoot service is running
  5. Flask app health endpoint responds
  6. Alembic migrations are up to date
  7. Git repo is owned by osint
  8. flask_session directory is writable by osint
  9. Python dependencies installed
 10. CMS_ENCRYPTION_KEY set in .env
"""

import argparse
import os
import pwd
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

APP_DIR = Path("/opt/osint-dashboard")
SF_DIR = Path("/opt/spiderfoot")
ENV_FILE = APP_DIR / ".env"
SF_SERVICE = "spiderfoot"
APP_SERVICE = "osint-dashboard"
OSINT_USER = "osint"
OSINT_HOME = Path("/home/osint")
SF_PASSWD = OSINT_HOME / ".spiderfoot" / "passwd"

OK = "  OK"
FAIL = "  FAIL"
WARN = "  WARN"
FIXED = "  FIXED"
SKIP = "  SKIP"
DRY = "  WOULD FIX"

# Detect venv Python (system Python 3.14 has PEP 668 externally-managed)
VENV_PYTHON = None
for candidate in [
    APP_DIR / "venv" / "bin" / "python3",
    Path("/opt/osint-dashboard/venv/bin/python3"),
    Path("/opt/osint-dashboard/.venv/bin/python3"),
]:
    if candidate.exists():
        VENV_PYTHON = str(candidate)
        break
if not VENV_PYTHON:
    VENV_PYTHON = shutil.which("python3") or "/usr/bin/python3"


def log(msg: str, status: str = "", **kwargs):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg} {status}", **kwargs)


def run(cmd: list, timeout: int = 30, **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, **kwargs
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, "", "TIMEOUT")


def check_home_dir(dry: bool) -> bool:
    log("Checking /home/osint...", end=" ")
    if not OSINT_HOME.exists():
        if dry:
            log(DRY + " (mkdir -p /home/osint && chown osint:osint)")
            return False
        OSINT_HOME.mkdir(parents=True, exist_ok=True)
        shutil.chown(OSINT_HOME, user=OSINT_USER, group=OSINT_USER)
        OSINT_HOME.chmod(0o755)
        log(FIXED)
        return True
    try:
        st = OSINT_HOME.stat()
        owner = pwd.getpwuid(st.st_uid).pw_name
        if owner != OSINT_USER:
            if dry:
                log(DRY + f" (chown osint:osint, currently {owner})")
                return False
            shutil.chown(OSINT_HOME, user=OSINT_USER, group=OSINT_USER)
            log(FIXED + f" (was {owner})")
            return True
        log(OK)
        return True
    except (KeyError, OSError) as e:
        log(FAIL + f" {e}")
        return False


def check_osint_user(dry: bool) -> bool:
    log("Checking osint user exists...", end=" ")
    try:
        pwd.getpwnam(OSINT_USER)
        log(OK)
        return True
    except KeyError:
        log(FAIL + " osint user does not exist")
        return False


def check_spiderfoot_dir(dry: bool) -> bool:
    log("Checking ~/.spiderfoot...", end=" ")
    sf_dir = OSINT_HOME / ".spiderfoot"
    if not sf_dir.exists():
        if dry:
            log(DRY + " (mkdir + chown osint:osint)")
            return False
        sf_dir.mkdir(parents=True, exist_ok=True)
        shutil.chown(sf_dir, user=OSINT_USER, group=OSINT_USER)
        log(FIXED)
        return True
    try:
        st = sf_dir.stat()
        owner = pwd.getpwuid(st.st_uid).pw_name
        if owner != OSINT_USER:
            if dry:
                log(DRY + f" (chown osint:osint, currently {owner})")
                return False
            shutil.chown(sf_dir, user=OSINT_USER, group=OSINT_USER)
            log(FIXED + f" (was {owner})")
        else:
            log(OK)
        return True
    except Exception as e:
        log(FAIL + f" {e}")
        return False


def check_spiderfoot_service(dry: bool) -> bool:
    log("Checking spiderfoot.service...", end=" ")
    r = run(["systemctl", "is-active", SF_SERVICE])
    if r.returncode == 0:
        log(OK + f" ({r.stdout.strip()})")
        return True
    log(FAIL + f" ({r.stdout.strip() or r.stderr.strip()})")
    # Try to start it
    if dry:
        log(f"  {DRY} (systemctl start {SF_SERVICE})")
        return False
    log("  Attempting to start...", end=" ")
    r2 = run(["systemctl", "start", SF_SERVICE])
    if r2.returncode == 0:
        log(FIXED)
        return True
    log(FAIL + f" {r2.stderr.strip()}")
    # Check logs for common errors
    r3 = run(["journalctl", "-u", SF_SERVICE, "-n", "20", "--no-pager"])
    if "Permission denied" in r3.stdout:
        log("  Detected: Permission denied in SF logs")
        path_line = [
            line for line in r3.stdout.split("\n") if "Permission denied" in line
        ]
        if path_line:
            log(f"  Path: {path_line[0].strip()}")
    return False


def check_flask_health(dry: bool) -> bool:
    log("Checking Flask health (curl localhost:5000/health?quick=1)...", end=" ")
    r = run(["curl", "-sf", "http://localhost:5000/health?quick=1"], timeout=10)
    if r.returncode == 0:
        log(OK)
        return True
    log(FAIL)
    if dry:
        return False
    # Try restarting the app
    log("  Restarting osint-dashboard.service...", end=" ")
    r2 = run(["systemctl", "restart", APP_SERVICE])
    if r2.returncode == 0:
        log(FIXED)
        return True
    log(FAIL + f" {r2.stderr.strip()}")
    return False


def check_alembic(dry: bool) -> bool:
    log("Checking Alembic migrations...", end=" ")
    env = {**os.environ}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    python = VENV_PYTHON

    def revision_ids(args: list) -> set | None:
        r = run([python, "-m", "alembic"] + args, cwd=str(APP_DIR), env=env, timeout=30)
        if r.returncode != 0:
            return None
        return set(
            line.split()[0] for line in (r.stdout or "").splitlines() if line.strip()
        )

    # Compare the database's current revision against the repo's heads. We must
    # NOT use 'alembic check' here: it reports model-vs-DB drift, not pending
    # migrations, so 'alembic upgrade head' could never actually fix it.
    current = revision_ids(["current"])
    heads = revision_ids(["heads"])
    if current is not None and heads is not None and current == heads:
        log(OK)
        return True
    # Try upgrade
    log(FAIL + " (pending migrations)")
    if dry:
        return False
    r2 = run(
        [python, "-m", "alembic", "upgrade", "head"],
        cwd=str(APP_DIR),
        env=env,
        timeout=60,
    )
    if r2.returncode == 0:
        log(f"  {FIXED} (upgrade OK)")
        return True
    log(f"  {FAIL} {r2.stderr.strip()[:200]}")
    return False


def check_git_perms(dry: bool) -> bool:
    log("Checking .git ownership...", end=" ")
    git_dir = APP_DIR / ".git"
    if not git_dir.exists():
        log(SKIP + " (no .git directory)")
        return True
    try:
        st = git_dir.stat()
        owner = pwd.getpwuid(st.st_uid).pw_name
        if owner != OSINT_USER:
            if dry:
                log(DRY + f" (chown -R osint:osint .git, currently {owner})")
                return False
            r = run(["chown", "-R", f"{OSINT_USER}:{OSINT_USER}", str(git_dir)])
            if r.returncode == 0:
                log(FIXED + f" (was {owner})")
            else:
                # Try with sudo
                r = run(
                    ["sudo", "chown", "-R", f"{OSINT_USER}:{OSINT_USER}", str(git_dir)]
                )
                if r.returncode == 0:
                    log(FIXED + f" (was {owner}, via sudo)")
                else:
                    log(FAIL + f" {r.stderr.strip()}")
                    return False
        else:
            log(OK)
        return True
    except Exception as e:
        log(FAIL + f" {e}")
        return False


def check_flask_session(dry: bool) -> bool:
    log("Checking flask_session/ writable...", end=" ")
    sess_dir = APP_DIR / "flask_session"
    if not sess_dir.exists():
        if dry:
            log(DRY + " (mkdir flask_session && chown osint)")
            return False
        sess_dir.mkdir(parents=True, exist_ok=True)
        shutil.chown(sess_dir, user=OSINT_USER, group=OSINT_USER)
        log(FIXED)
        return True
    try:
        st = sess_dir.stat()
        owner = pwd.getpwuid(st.st_uid).pw_name
        if owner != OSINT_USER:
            if dry:
                log(DRY + f" (chown osint:osint, currently {owner})")
                return False
            shutil.chown(sess_dir, user=OSINT_USER, group=OSINT_USER)
            log(FIXED + f" (was {owner})")
        else:
            # Also check writable
            if os.access(str(sess_dir), os.W_OK):
                log(OK)
            else:
                log(FAIL + " not writable by current user")
                return False
        return True
    except Exception as e:
        log(FAIL + f" {e}")
        return False


def check_pip_deps(dry: bool) -> bool:
    log("Checking Python dependencies...", end=" ")
    req = APP_DIR / "requirements.txt"
    if not req.exists():
        log(SKIP + " (no requirements.txt)")
        return True
    python = VENV_PYTHON
    r = run([python, "-m", "pip", "install", "-r", str(req), "--dry-run"], timeout=60)
    if r.returncode == 0:
        log(OK)
        return True
    log(FAIL)
    if dry:
        return False
    r2 = run([python, "-m", "pip", "install", "-r", str(req), "--upgrade"], timeout=120)
    if r2.returncode == 0:
        log(f"  {FIXED}")
        return True
    log(f"  {FAIL} {r2.stderr.strip()[:200]}")
    return False


def check_env_encryption_key(dry: bool) -> bool:
    log("Checking CMS_ENCRYPTION_KEY in .env...", end=" ")
    if not ENV_FILE.exists():
        log(FAIL + " (.env not found)")
        return False
    content = ENV_FILE.read_text()
    if "CMS_ENCRYPTION_KEY" in content:
        log(OK)
        return True
    log(FAIL + " (CMS_ENCRYPTION_KEY not set)")
    return False


def check_env_encryption_keys(dry: bool) -> bool:
    log("Checking CMS_ENCRYPTION_KEYS (legacy keys)...", end=" ")
    if not ENV_FILE.exists():
        log(FAIL + " (.env not found)")
        return False
    content = ENV_FILE.read_text()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("CMS_ENCRYPTION_KEYS="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value and not value.startswith("#"):
                keys = [k.strip() for k in value.split(",") if k.strip()]
                log(f"{OK} ({len(keys)} legacy key(s) configured)")
                return True
    log(OK + " (no legacy keys — all data encrypted with current key)")
    return True


def check_spiderfoot_url_settings(dry: bool) -> bool:
    log("Checking spiderfoot_url setting in DB...", end=" ")
    env = {**os.environ}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    python = VENV_PYTHON
    code = (
        "from app import app; from cms.models import Setting; "
        "app.app_context().push(); "
        "v = Setting.get('spiderfoot_url', ''); "
        "print(v or 'EMPTY')"
    )
    r = run([python, "-c", code], cwd=str(APP_DIR), env=env, timeout=30)
    val = r.stdout.strip()
    if val and val != "EMPTY":
        log(OK + f" ({val})")
        return True
    log(FAIL + " (not configured)")
    if dry:
        return False
    # Auto-configure to default
    set_code = (
        "from app import app; from cms.models import Setting; "
        "app.app_context().push(); "
        "Setting.set('spiderfoot_url', 'http://127.0.0.1:5001', 'SpiderFoot server URL', 'spiderfoot'); "
        "print('OK')"
    )
    r2 = run([VENV_PYTHON, "-c", set_code], cwd=str(APP_DIR), env=env, timeout=30)
    if r2.returncode == 0:
        log(f"  {FIXED} (set to http://127.0.0.1:5001)")
        return True
    log(f"  {FAIL} {r2.stderr.strip()[:200]}")
    return False


def check_ssl_renewal(dry: bool) -> bool:
    log("Checking certbot SSL renewal timer...", end=" ")
    r = run(["systemctl", "is-active", "certbot.timer"])
    if r.returncode == 0 and r.stdout.strip() == "active":
        log(OK + f" ({r.stdout.strip()})")
        return True
    r = run(["systemctl", "is-active", "certbot-renewal.timer"])
    if r.returncode == 0:
        log(OK + f" ({r.stdout.strip()})")
        return True
    log(FAIL + " (no certbot timer found)")
    if dry:
        return False
    r2 = run(["which", "certbot"])
    if r2.returncode != 0:
        log("  certbot not installed — skipping")
        return True
    r3 = run(["systemctl", "enable", "certbot.timer", "--now"])
    if r3.returncode == 0:
        log(f"  {FIXED} (certbot.timer enabled)")
        return True
    return False


def check_backup_cron(dry: bool) -> bool:
    log("Checking backup cron (4x daily)...", end=" ")
    cron_file = Path("/etc/cron.d/osint-dashboard-backup")
    if cron_file.exists():
        content = cron_file.read_text()
        if "0,6,12,18" in content or all(
            f"0 {h}" in content for h in ("0", "6", "12", "18")
        ):
            log(OK)
            return True
        log(FAIL + " (wrong schedule)")
    else:
        log(FAIL + " (not installed)")
    backup_script = APP_DIR / "scripts" / "backup.sh"
    if not backup_script.exists():
        log("  backup.sh not found — skipping")
        return True
    if dry:
        log(f"  {DRY} (install /etc/cron.d/osint-dashboard-backup)")
        return False
    cron_content = (
        f"0 0,6,12,18 * * * osint {backup_script} {APP_DIR}/backups > /dev/null 2>&1\n"
    )
    cron_file.write_text(cron_content)
    cron_file.chmod(0o644)
    log(f"  {FIXED}")
    return True


def check_weasyprint_deps(dry: bool) -> bool:
    log("Checking weasyprint system deps (libpango)...", end=" ")
    r = run(["ldconfig", "-p"], timeout=10)
    if "libpango" in r.stdout:
        log(OK)
        return True
    log(FAIL + " (install: libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0)")
    return False


def check_opsec(dry: bool) -> bool:
    log("Checking OPSEC settings (Tor, stealth, audit chain)...", end=" ")
    env = {**os.environ}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    python = VENV_PYTHON
    code = (
        "from app import app\n"
        "from cms.opsec_check import run_opsec_checks\n"
        "app.app_context().push()\n"
        "r = run_opsec_checks(verbose=False)\n"
        "print('PASS' if r['pass'] else 'FAIL')\n"
        "for n, c in sorted(r['checks'].items()): print(f\"  {n}: {'PASS' if c['pass'] else 'FAIL'} {c['detail'][:80]}\")\n"
    )
    r = run([python, "-c", code], cwd=str(APP_DIR), env=env, timeout=30)
    if "PASS" in r.stdout and "FAIL" not in r.stdout:
        log(OK)
        return True
    log(FAIL + " (issues found — run: flask opsec:check for details)")
    print(r.stdout[:400])
    return False


def check_playwright(dry: bool) -> bool:
    log("Checking Playwright Chromium...", end=" ")
    candidates = [
        Path.home() / ".cache" / "ms-playwright",
        Path("/home/osint/.cache/ms-playwright"),
    ]
    for chromium_path in candidates:
        if chromium_path.exists() and any(chromium_path.iterdir()):
            log(OK)
            return True
    log(
        FAIL
        + " (run: sudo -u osint /opt/osint-dashboard/venv/bin/python3 -m playwright install chromium)"
    )
    return False


def check_default_password(dry: bool) -> bool:
    log("Checking default admin password...", end=" ")
    env = {**os.environ}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    python = VENV_PYTHON
    code = (
        "from app import app; from cms.models import User; "
        "app.app_context().push(); "
        "u = User.query.filter_by(username='admin').first(); "
        "print('DEFAULT' if u and u.password_hash and "
        "u.password_hash.startswith('scrypt') and "
        "u.check_password('changeme123') else 'OK')"
    )
    r = run([python, "-c", code], cwd=str(APP_DIR), env=env, timeout=15)
    if "DEFAULT" in r.stdout:
        log(FAIL + " (admin:changeme123 — change immediately!)")
        return False
    log(OK)
    return True


def _set_env(key: str, value: str) -> None:
    """Append or replace a KEY=value line in .env (preserves other lines)."""
    lines = ENV_FILE.read_text().splitlines()
    out, found = [], False
    for line in lines:
        if line.strip().startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def _env_value(key: str) -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text().splitlines():
        if line.strip().startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def check_env_flask_env(dry: bool) -> bool:
    log("Checking FLASK_ENV in .env...", end=" ")
    if not ENV_FILE.exists():
        log(FAIL + " (.env not found)")
        return False
    val = _env_value("FLASK_ENV")
    if val == "production":
        log(OK)
        return True
    if dry:
        log(
            DRY
            + (
                f" (FLASK_ENV={val}, should be production)"
                if val
                else " (FLASK_ENV not set)"
            )
        )
        return False
    _set_env("FLASK_ENV", "production")
    log(FIXED + (f" (was {val})" if val else ""))
    return True


def check_env_db_ssl_mode(dry: bool) -> bool:
    log("Checking DB_SSL_MODE in .env...", end=" ")
    if not ENV_FILE.exists():
        log(SKIP + " (.env not found)")
        return True
    val = _env_value("DB_SSL_MODE")
    if val:
        if val in ("require", "verify-ca", "verify-full"):
            log(OK)
            return True
        log(WARN + f" ({val!r} is below the production TLS floor)")
        if dry:
            return False
        _set_env("DB_SSL_MODE", "require")
        log(FIXED)
        return True
    if dry:
        log(DRY + " (DB_SSL_MODE not set — production defaults to 'require')")
        return False
    _set_env("DB_SSL_MODE", "require")
    log(FIXED)
    return True


def check_redis(dry: bool) -> bool:
    log("Checking Redis...", end=" ")
    # Redis is optional: the app only uses it when REDIS_URL is set in .env.
    if not _env_value("REDIS_URL"):
        log(SKIP + " (REDIS_URL not set — session backend is filesystem)")
        return True
    if not shutil.which("redis-cli"):
        log(FAIL + " (redis-cli not installed)")
        return False
    r = run(["redis-cli", "ping"], timeout=5)
    if r.returncode == 0 and "PONG" in r.stdout:
        log(OK)
        return True
    log(FAIL + " (redis-server not running)")
    return False


def check_gunicorn_logging(dry: bool) -> bool:
    log("Checking gunicorn error log directory...", end=" ")
    log_dir = Path("/var/log/osint-dashboard")
    if log_dir.exists():
        st = log_dir.stat()
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = "unknown"
        if owner == OSINT_USER:
            log(OK)
        else:
            if dry:
                log(DRY + f" (chown osint:osint, currently {owner})")
                return False
            shutil.chown(str(log_dir), user=OSINT_USER, group=OSINT_USER)
            log(FIXED + f" (was {owner})")
        return True
    log(FAIL + " (/var/log/osint-dashboard not found)")
    if dry:
        log(f"  {DRY} (mkdir -p /var/log/osint-dashboard && chown osint:osint)")
        return False
    log_dir.mkdir(parents=True, exist_ok=True)
    shutil.chown(str(log_dir), user=OSINT_USER, group=OSINT_USER)
    log(f"  {FIXED}")
    return True


def main():
    parser = argparse.ArgumentParser(description="OSINT Dashboard doctor")
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be fixed without making changes",
    )
    args = parser.parse_args()
    dry = args.dry_run

    if os.geteuid() != 0:
        print("This script should be run as root for full diagnostics.")
        print("Re-run with: sudo python3 scripts/doctor.py")
        if not dry:
            sys.exit(1)

    print(f"\n{'=' * 60}")
    print("  OSINT Dashboard — Server Diagnostics")
    print(
        f"  {'DRY RUN — no changes will be made' if dry else 'Fixing issues automatically'}"
    )
    print(f"{'=' * 60}\n")

    checks = [
        ("osint user", check_osint_user),
        ("home directory", check_home_dir),
        ("~/.spiderfoot directory", check_spiderfoot_dir),
        (".git ownership", check_git_perms),
        ("flask_session/ writable", check_flask_session),
        ("Python dependencies", check_pip_deps),
        (".env CMS_ENCRYPTION_KEY", check_env_encryption_key),
        (".env CMS_ENCRYPTION_KEYS", check_env_encryption_keys),
        ("Alembic migrations", check_alembic),
        ("spiderfoot.service", check_spiderfoot_service),
        ("Flask health", check_flask_health),
        ("SF URL in Settings", check_spiderfoot_url_settings),
        ("SSL cert renewal", check_ssl_renewal),
        ("Backup cron", check_backup_cron),
        ("Gunicorn error log", check_gunicorn_logging),
        ("weasyprint deps", check_weasyprint_deps),
        ("OPSEC settings", check_opsec),
        ("Playwright", check_playwright),
        ("Default admin password", check_default_password),
        ("FLASK_ENV=production", check_env_flask_env),
        ("DB_SSL_MODE in .env", check_env_db_ssl_mode),
        ("Redis", check_redis),
    ]

    good = bad = 0
    for name, func in checks:
        print(f"\n  [{name}]")
        if func(dry):
            good += 1
        else:
            bad += 1

    print(f"\n{'=' * 60}")
    print(f"  Results: {good}/{len(checks)} passed, {bad} failed")
    if bad == 0:
        print("  All checks passed!")
    else:
        if dry:
            print("  Re-run without --dry-run to fix issues")
        else:
            print("  Some issues could not be auto-fixed — check output above")
    print(f"{'=' * 60}\n")
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
