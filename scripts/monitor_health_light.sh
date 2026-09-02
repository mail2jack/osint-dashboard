#!/usr/bin/env bash
# Lightweight production health collector. Reads only new journal events.
set -euo pipefail

DIR=${DIR:-/opt/osint-dashboard}
OUT=${OUT:-$DIR/reports/monitoring/health-light.csv}
STATE=${STATE:-/tmp/osint-health-light}
SERVICE=${SERVICE:-osint-dashboard}
INTERVAL=${INTERVAL:-300}

mkdir -p "$(dirname "$OUT")" "$STATE"
if [[ ! -f "$OUT" ]]; then
    printf 'ts,oserror_delta_5m,health_quick_s,store_foreign,store_total,active_enter,restart_since_last\n' > "$OUT"
fi

cursor_file="$STATE/journal.cursor"
active_file="$STATE/active_enter"
event_file="$STATE/events"

if [[ ! -s "$cursor_file" ]]; then
    journalctl -u "$SERVICE" -n 1 --show-cursor --no-pager 2>/dev/null \
        | awk -F'-- cursor: ' '/-- cursor:/ {print $2}' > "$cursor_file" || true
fi

while :; do
    stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    started=$(date +%s.%N)
    curl -sS -o /dev/null -w '%{time_total}' --max-time 5 \
        'http://127.0.0.1:5000/health?quick=1' > "$STATE/health_time" || true
    health=$(cat "$STATE/health_time")
    finished=$(date +%s.%N)
    : "${started}${finished}"

    journal_args=(-u "$SERVICE" --no-pager --show-cursor -o cat)
    if [[ -s "$cursor_file" ]]; then
        journal_args+=(--after-cursor "$(cat "$cursor_file")")
    fi
    journalctl "${journal_args[@]}" > "$event_file" 2>/dev/null || true
    oserror=$(grep -cE 'OSError' "$event_file" || true)
    new_cursor=$(awk -F'-- cursor: ' '/-- cursor:/ {print $2}' "$event_file" | tail -1)
    if [[ -n "$new_cursor" ]]; then
        printf '%s\n' "$new_cursor" > "$cursor_file"
    fi

    foreign=$(find "$DIR/flask_session" -maxdepth 1 -type f ! -user osint 2>/dev/null | wc -l | tr -d ' ')
    total=$(find "$DIR/flask_session" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
    active=$(systemctl show "$SERVICE" --property=ActiveEnterTimestampMonotonic --value)
    previous=$(cat "$active_file" 2>/dev/null || true)
    restart=0
    if [[ -n "$previous" && "$active" != "$previous" ]]; then
        restart=1
    fi
    printf '%s,%s,%s,%s,%s,%s,%s\n' \
        "$stamp" "$oserror" "$health" "$foreign" "$total" "$active" "$restart" >> "$OUT"
    printf '%s\n' "$active" > "$active_file"
    sleep "$INTERVAL"
done
