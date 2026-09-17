#!/usr/bin/env bash
# Install the explicitly managed Gunicorn override without restarting the app.
# The operator must review the result and restart osint-dashboard separately.
set -euo pipefail

# Production defaults are fixed. APP_DIR/DST_BASE are overridable ONLY by the
# test harness to point at a sandbox with shims; the deployed installer always
# resolves to /opt/osint-dashboard and /etc/systemd/system and executes the
# real venv binary.
APP_DIR="${APP_DIR:-/opt/osint-dashboard}"
SRC="$APP_DIR/deploy/osint-dashboard-gunicorn2.override.conf"
SERVICE="osint-dashboard.service"
DST_BASE="${DST_BASE:-/etc/systemd/system}"
DST_DIR="$DST_BASE/$SERVICE.d"
DST="$DST_DIR/override.conf"
GUNICORN="$APP_DIR/venv/bin/gunicorn"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$DST.backup-$STAMP"
TMP=""

cleanup() {
    if [ -n "$TMP" ] && [ -e "$TMP" ]; then
        rm -f "$TMP"
    fi
}
trap cleanup EXIT

restore_previous() {
    if [ -f "$BACKUP" ]; then
        install -o root -g root -m 0644 "$BACKUP" "$DST"
    else
        rm -f "$DST"
    fi
    systemctl daemon-reload
}

# Drift comparison: whitespace-collapsed, flag-stripped equality. The ONLY
# permitted difference from the repo source is an absent --no-control-socket
# (the known pre-fix legacy variant). This must never degrade into a loose
# "contains" match; any other content is an unexpected operator change.
flagless() {
    sed 's/--no-control-socket//g' "$1"
}

same_override() {
    local a b
    a="$( flagless "$1" | tr -s '[:space:]' ' ' | sed 's/^ *//;s/ *$//' )"
    b="$( flagless "$2" | tr -s '[:space:]' ' ' | sed 's/^ *//;s/ *$//' )"
    [ "$a" = "$b" ]
}

# Read-only, fail-closed capability guard against the installed venv binary at
# the fixed path. Runs `$GUNICORN --help` only. On a missing binary, a failing
# help run, or an absent --no-control-socket it returns non-zero BEFORE any
# filesystem, systemd, service, or control-socket change. It never starts a
# service and prints no secrets.
gunicorn_supports_flag() {
    local help
    if [ ! -x "$GUNICORN" ]; then
        echo "ERROR: installed gunicorn not executable: $GUNICORN" >&2
        return 1
    fi
    if ! help="$( "$GUNICORN" --help 2>&1 )"; then
        echo "ERROR: cannot run '$GUNICORN --help'; aborting before any change" >&2
        return 1
    fi
    if ! printf '%s\n' "$help" | grep -Fq -- '--no-control-socket'; then
        echo "ERROR: installed gunicorn does not support --no-control-socket; aborting before any change" >&2
        return 1
    fi
}

run_install() {
    if [ ! -f "$SRC" ]; then
        echo "ERROR: source override not found: $SRC" >&2
        return 1
    fi
    gunicorn_supports_flag || return 1

    # Drift guard, before any change at all. The live override may only be the
    # exact repo source or the known legacy variant missing solely the
    # control-socket flag. Any other content is an unexpected operator change:
    # nothing is written, backed up, reloaded, or restarted until a human
    # reviews it.
    if [ -e "$DST" ] && ! same_override "$SRC" "$DST"; then
        echo "ERROR: existing $DST deviates unexpectedly from the managed override" >&2
        echo "The only accepted contents are the exact repo source or the legacy" >&2
        echo "variant that differs solely by an absent --no-control-socket." >&2
        echo "Nothing was written, backed up, reloaded, or restarted. Review manually." >&2
        return 1
    fi

    if [ ! -d "$DST_DIR" ]; then
        install -d -o root -g root -m 0755 "$DST_DIR"
    fi
    if [ -e "$DST" ]; then
        if [ -e "$BACKUP" ]; then
            echo "ERROR: backup already exists: $BACKUP" >&2
            return 1
        fi
        cp -p "$DST" "$BACKUP"
        echo "Backup: $BACKUP"
    fi

    # Atomically install via a temp file in the target directory.
    TMP="$(mktemp "$DST_DIR/.override.conf.tmp.XXXXXX")"
    install -o root -g root -m 0644 "$SRC" "$TMP"
    mv -f "$TMP" "$DST"
    TMP=""

    if ! systemctl daemon-reload; then
        echo "ERROR: systemd daemon-reload failed; restoring prior override" >&2
        restore_previous
        return 1
    fi
    if command -v systemd-analyze >/dev/null 2>&1; then
        if ! systemd-analyze verify "$SERVICE"; then
            echo "ERROR: systemd unit verification failed; restoring prior override" >&2
            restore_previous
            return 1
        fi
    else
        echo "WARNING: systemd-analyze unavailable; unit not verified" >&2
    fi

    echo "OK: installed $DST"
    echo "IMPORTANT: no service restart was performed."
    echo "Review the unit, then explicitly run: systemctl restart $SERVICE"
}

# Sourceable guard: when this file is `source`d by the test harness the
# functions above are available (with APP_DIR/DST_BASE overridden to a sandbox)
# but nothing executes. When run as a script, the operator flow runs.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "ERROR: run as root (sudo ./scripts/install_dashboard_gunicorn_override.sh)" >&2
        exit 1
    fi
    run_install
fi
