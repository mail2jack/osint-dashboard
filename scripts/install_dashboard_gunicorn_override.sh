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

# Drift comparison: whitespace-normalized exact equality. Apart from the
# managed source, there is precisely one accepted historical live variant:
# it lacks BOTH additions introduced by this change,
# --no-control-socket and Environment=LOG_FILE=/dev/null. A partial variant
# (only one addition absent) or any other difference is unexpected drift.
normalize_override() {
    tr -s '[:space:]' ' ' | sed 's/^ *//;s/ *$//'
}

known_legacy_source() {
    sed \
        -e 's/--no-control-socket//g' \
        -e '/^[[:space:]]*Environment=LOG_FILE=\/dev\/null[[:space:]]*$/d' \
        "$1" | normalize_override
}

same_override() {
    local managed live legacy
    managed="$(normalize_override < "$1")"
    live="$(normalize_override < "$2")"
    [ "$managed" = "$live" ] && return 0

    # The legacy variant must omit both additions. Do not normalize those out
    # of the live file: that would accidentally accept a partial or modified
    # override.
    if grep -Fq -- '--no-control-socket' "$2" \
        || grep -Eq '^[[:space:]]*Environment=LOG_FILE=/dev/null[[:space:]]*$' "$2"; then
        return 1
    fi
    legacy="$(known_legacy_source "$1")"
    [ "$legacy" = "$live" ]
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
    # exact repo source or the known legacy variant missing both managed
    # additions. Any other content is an unexpected operator change:
    # nothing is written, backed up, reloaded, or restarted until a human
    # reviews it.
    if [ -e "$DST" ] && ! same_override "$SRC" "$DST"; then
        echo "ERROR: existing $DST deviates unexpectedly from the managed override" >&2
        echo "The only accepted contents are the exact repo source or the legacy" >&2
        echo "variant that lacks both --no-control-socket and LOG_FILE=/dev/null." >&2
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

# Fail-closed fixed-path guard for deployed/direct execution. In production the
# source, Gunicorn binary, backup root, and systemd drop-in root are always the
# same fixed paths. APP_DIR/DST_BASE overrides are honored ONLY when this file
# is `source`d by the test harness (which drives run_install against a sandbox).
# A direct run handed a deviant APP_DIR or DST_BASE is refused before the root
# check and before any access to the source file, venv --help, backup, write,
# daemon-reload, or systemd-analyze.
guard_fixed_paths() {
    if [ "$APP_DIR" != "/opt/osint-dashboard" ]; then
        echo "ERROR: APP_DIR must be /opt/osint-dashboard in a deployed run (got: $APP_DIR); refusing" >&2
        return 1
    fi
    if [ "$DST_BASE" != "/etc/systemd/system" ]; then
        echo "ERROR: DST_BASE must be /etc/systemd/system in a deployed run (got: $DST_BASE); refusing" >&2
        return 1
    fi
    return 0
}

# Sourceable guard: when this file is `source`d by the test harness the
# functions above are available (with APP_DIR/DST_BASE overridden to a sandbox)
# but nothing executes. When run as a script, the operator flow runs.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    guard_fixed_paths || exit 1
    if [ "$(id -u)" -ne 0 ]; then
        echo "ERROR: run as root (sudo ./scripts/install_dashboard_gunicorn_override.sh)" >&2
        exit 1
    fi
    run_install
fi
