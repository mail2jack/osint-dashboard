#!/usr/bin/env bash
# Install a root-owned Chromium bundle and its narrowly attached AppArmor
# user-namespace profile. This never enables or starts the capture worker.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/osint-dashboard}"
CAPTURE_ROOT="${CAPTURE_ROOT:-/opt/osint-capture}"
APPARMOR_DIR="${APPARMOR_DIR:-/etc/apparmor.d}"
ENV_FILE="${ENV_FILE:-/etc/default/osint-finding-capture}"
TEMPLATE="$APP_DIR/deploy/osint-finding-capture-chromium.apparmor.in"
PROFILE="$APPARMOR_DIR/osint-finding-capture-chromium"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

guard_fixed_paths() {
    [ "$APP_DIR" = "/opt/osint-dashboard" ] || { echo "ERROR: APP_DIR is fixed" >&2; return 1; }
    [ "$CAPTURE_ROOT" = "/opt/osint-capture" ] || { echo "ERROR: CAPTURE_ROOT is fixed" >&2; return 1; }
    [ "$APPARMOR_DIR" = "/etc/apparmor.d" ] || { echo "ERROR: APPARMOR_DIR is fixed" >&2; return 1; }
    [ "$ENV_FILE" = "/etc/default/osint-finding-capture" ] || { echo "ERROR: ENV_FILE is fixed" >&2; return 1; }
}

find_root_owned_chromium() {
    local candidate
    for candidate in "$CAPTURE_ROOT"/chromium-*/chrome-linux64/chrome; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo "ERROR: no root-installed Playwright Chromium bundle found" >&2
    return 1
}

verify_root_owned_binary() {
    local binary="$1" bundle owner mode
    bundle="$(dirname "$(dirname "$binary")")"
    owner="$(stat -c '%U:%G' "$binary")"
    mode="$(stat -c '%a' "$binary")"
    [ "$owner" = "root:root" ] || { echo "ERROR: browser is not root-owned" >&2; return 1; }
    case "$mode" in
        *2|*3|*6|*7) echo "ERROR: browser is writable by group or others" >&2; return 1 ;;
    esac
    [ -x "$binary" ] || { echo "ERROR: browser is not executable" >&2; return 1; }
    if find "$bundle" -xdev \( -type f -o -type d \) -perm /022 -print -quit | grep -q .; then
        echo "ERROR: browser bundle is writable by group or others" >&2
        return 1
    fi
}

install_root_owned_chromium() {
    install -d -o root -g root -m 0755 "$CAPTURE_ROOT"
    if find_root_owned_chromium >/dev/null; then
        return 0
    fi
    PLAYWRIGHT_BROWSERS_PATH="$CAPTURE_ROOT" "$APP_DIR/venv/bin/python3" -m playwright install chromium
}

backup_if_present() {
    local path="$1"
    if [ -e "$path" ]; then
        cp -p "$path" "$path.backup-$STAMP"
        echo "Backup: $path.backup-$STAMP"
    fi
}

run_install() {
    local chromium binary tmp_profile tmp_env
    [ -f "$TEMPLATE" ] || { echo "ERROR: profile template unavailable" >&2; return 1; }
    command -v apparmor_parser >/dev/null || { echo "ERROR: apparmor_parser unavailable" >&2; return 1; }
    install_root_owned_chromium
    chromium="$(find_root_owned_chromium)"
    binary="$chromium"
    verify_root_owned_binary "$binary"
    backup_if_present "$PROFILE"
    backup_if_present "$ENV_FILE"

    tmp_profile="$(mktemp "$APPARMOR_DIR/.osint-capture.XXXXXX")"
    sed "s|@CHROMIUM_PATH@|$binary|g" "$TEMPLATE" > "$tmp_profile"
    install -o root -g root -m 0644 "$tmp_profile" "$PROFILE"
    rm -f "$tmp_profile"
    apparmor_parser -r "$PROFILE"

    tmp_env="$(mktemp "$(dirname "$ENV_FILE")/.osint-capture.XXXXXX")"
    printf 'FINDING_CAPTURE_CHROMIUM_PATH=%s\n' "$binary" > "$tmp_env"
    install -o root -g root -m 0644 "$tmp_env" "$ENV_FILE"
    rm -f "$tmp_env"
    systemctl daemon-reload
    echo "OK: AppArmor capture-browser profile installed for $binary"
    echo "IMPORTANT: worker remains disabled and was not started."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    guard_fixed_paths || exit 1
    [ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root" >&2; exit 1; }
    run_install
fi
