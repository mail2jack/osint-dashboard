#!/usr/bin/env bash
# Guided production rollout. No automatic rollback and no production restore.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/osint-dashboard}"
CONFIRM=false
DRY_RUN=false
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="${ROLLOUT_EVIDENCE_DIR:-$APP_DIR/reports/rollout}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/iveras-rollout.XXXXXX")"
CHECKS_FILE="$WORK_DIR/checks.tsv"
REPORT_PATH="$EVIDENCE_DIR/rollout-$TIMESTAMP.json"
ERRORS=0

cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

usage() {
    cat <<'EOF'
Usage: production_rollout.sh [--dry-run | --confirm DEPLOY-MASTER]

  --dry-run                  Read-only checks; performs no deployment or purge.
  --confirm DEPLOY-MASTER    Authorize the real production rollout.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --confirm) [ "${2:-}" = "DEPLOY-MASTER" ] || { usage >&2; exit 2; }; CONFIRM=true; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

record() {
    local name="$1" result="$2" detail="$3"
    printf '%s\t%s\t%s\n' "$name" "$result" "$detail" >> "$CHECKS_FILE"
    [ "$result" = pass ] || ERRORS=$((ERRORS + 1))
}

if [ "$(id -u)" -ne 0 ]; then
    printf 'Run dit script op de VPS met sudo/root.\n' >&2
    exit 2
fi
if [ ! -d "$APP_DIR" ]; then
    printf 'Applicatiemap ontbreekt: %s\n' "$APP_DIR" >&2
    exit 2
fi

BRANCH="$(sudo -u osint git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [ "$BRANCH" = master ]; then
    record repository pass "branch=master"
else
    record repository fail "expected branch=master actual=$BRANCH"
fi

for required in \
    "$APP_DIR/scripts/deploy.sh" \
    "$APP_DIR/scripts/update.sh" \
    "$APP_DIR/scripts/backup.sh" \
    "$APP_DIR/license-server/deploy/deploy.sh" \
    "$APP_DIR/license-server/deploy/license-server-privacy-purge.timer"; do
    if [ -f "$required" ]; then
        record "file_$(basename "$required")" pass "present"
    else
        record "file_$(basename "$required")" fail "missing"
    fi
done

if [ "$DRY_RUN" = true ]; then
    if [ "$ERRORS" -eq 0 ]; then
        printf 'DRY RUN PASSED: er wordt niets gewijzigd.\n'
        exit 0
    fi
    printf 'DRY RUN FAILED: los eerst de gemelde controles op.\n' >&2
    exit 1
fi
if [ "$CONFIRM" != true ] || [ "$ERRORS" -ne 0 ]; then
    printf 'Rollout gestopt: gebruik --confirm DEPLOY-MASTER na een groene dry-run.\n' >&2
    exit 2
fi

START_SHA="$(sudo -u osint git -C "$APP_DIR" rev-parse HEAD)"
if sudo bash "$APP_DIR/scripts/deploy.sh"; then
    record app_deploy pass "existing deploy flow completed"
else
    record app_deploy fail "existing deploy flow failed; no automatic rollback"
fi

if sudo bash "$APP_DIR/license-server/deploy/deploy.sh" "$APP_DIR/license-server"; then
    record license_deploy pass "license-server deploy completed"
else
    record license_deploy fail "license-server deploy failed"
fi

if systemctl is-active --quiet osint-dashboard; then record app_service pass active; else record app_service fail inactive; fi
if systemctl is-active --quiet license-server; then record license_service pass active; else record license_service fail inactive; fi
if curl -fsS http://127.0.0.1:5000/health >/dev/null 2>&1; then record app_health pass healthy; else record app_health fail unhealthy; fi
if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then record license_health pass healthy; else record license_health fail unhealthy; fi
if systemctl is-enabled --quiet license-server-privacy-purge.timer \
    && systemctl is-active --quiet license-server-privacy-purge.timer; then
    record privacy_timer pass active
else
    record privacy_timer fail "timer is not enabled and active"
fi

check_env() {
    local name="$1" expected="$2" actual
    actual="$(awk -F= -v key="$name" '$1 == key {print $2; exit}' /opt/license-server/.env 2>/dev/null || true)"
    if [ "$actual" = "$expected" ]; then record "privacy_$name" pass "$expected"; else record "privacy_$name" fail "expected=$expected"; fi
}
check_env LICENSE_GEO_SOURCE off
check_env LICENSE_PTR_SOURCE off
check_env LICENSE_RDAP_SOURCE off

if sudo -u license env HOME=/opt/license-server \
    /opt/license-server/venv/bin/python3 /opt/license-server/cli.py privacy:purge \
    >/dev/null 2>&1; then
    record privacy_purge pass completed
else
    record privacy_purge fail failed
fi

COMMIT_SHA="$(sudo -u osint git -C "$APP_DIR" rev-parse HEAD)"
STATUS=pass
[ "$ERRORS" -eq 0 ] || STATUS=fail
python3 "$APP_DIR/scripts/rollout_report.py" \
    --output "$REPORT_PATH" \
    --commit-sha "$COMMIT_SHA" \
    --checks-file "$CHECKS_FILE" \
    --status "$STATUS"
printf 'Rolloutrapport: %s\n' "$REPORT_PATH"
if [ "$STATUS" != pass ]; then exit 1; fi
printf 'Rollout geslaagd. Vorige commit was %s.\n' "$START_SHA"
