#!/usr/bin/env bash
# Rebuild the application venv atomically, retaining a rollback copy.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/osint-dashboard}
VENV="$APP_DIR/venv"
LOCK="$APP_DIR/.venv-rebuild.lock"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TMP_VENV="$APP_DIR/.venv.rebuild.$STAMP"
OLD_VENV="$APP_DIR/venv.previous.$STAMP"
switched=false

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

exec 9>"$LOCK"
flock -n 9 || { echo "Another venv rebuild is running." >&2; exit 1; }
trap 'rm -rf "$TMP_VENV"' EXIT

python3 -m venv "$TMP_VENV"
"$TMP_VENV/bin/pip" install --upgrade pip setuptools wheel
"$TMP_VENV/bin/pip" install -r "$APP_DIR/requirements-lock.txt"
browser_path=$(sudo -u osint HOME=/home/osint "$TMP_VENV/bin/python3" -c \
    "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()")
if [[ ! -x "$browser_path" ]]; then
    echo "Required Playwright browser is unavailable: $browser_path" >&2
    exit 1
fi
"$TMP_VENV/bin/python3" -c "import app; print('application import: ok')"
"$TMP_VENV/bin/pip" check

was_active=false
if systemctl is-active --quiet osint-dashboard; then
    was_active=true
    systemctl stop osint-dashboard
fi

mv "$VENV" "$OLD_VENV"
mv "$TMP_VENV" "$VENV"
switched=true
chown -R osint:osint "$VENV"

rollback() {
    [[ "$switched" == true ]] || return 0
    rm -rf "$VENV"
    mv "$OLD_VENV" "$VENV"
    if [[ "$was_active" == true ]]; then
        systemctl start osint-dashboard || true
    fi
}
trap rollback ERR

if [[ "$was_active" == true ]]; then
    systemctl start osint-dashboard
    sleep 3
    curl -fsS --max-time 10 http://127.0.0.1:5000/api/v1/health >/dev/null
fi

rm -rf "$OLD_VENV"
trap - EXIT
echo "Virtualenv rebuilt successfully: $VENV"
