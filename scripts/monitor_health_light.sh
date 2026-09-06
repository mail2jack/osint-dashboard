#!/usr/bin/env bash
# Lightweight production health collector. Reads only new journal events.
set -euo pipefail

DIR=${DIR:-/opt/osint-dashboard}
OUT=${OUT:-$DIR/reports/monitoring/health-light.csv}
STATE=${STATE:-/tmp/osint-health-light}
SERVICE=${SERVICE:-osint-dashboard}
INTERVAL=${INTERVAL:-300}
REDIS_PASS_FILE=${REDIS_PASS_FILE:-/etc/redis/redis-pass}
REDIS_ALERT_TO=${REDIS_ALERT_TO:-server_update@iveras.com}

mkdir -p "$(dirname "$OUT")" "$STATE"
if [[ ! -f "$OUT" ]]; then
    printf 'ts,oserror_delta_5m,health_quick_s,store_foreign,store_total,active_enter,restart_since_last,redis\n' > "$OUT"
fi

cursor_file="$STATE/journal.cursor"
active_file="$STATE/active_enter"
redis_state_file="$STATE/redis_state"
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

    redis_state="-"
    if [[ -r "$REDIS_PASS_FILE" ]] && command -v redis-cli >/dev/null 2>&1; then
        redis_auth=$(cat "$REDIS_PASS_FILE")
        if REDISCLI_AUTH="$redis_auth" timeout 3 redis-cli -h 127.0.0.1 -p 6379 \
            --no-auth-warning ping >/dev/null 2>&1; then
            redis_state="ok"
        else
            redis_state="FAIL"
        fi
        prev_redis=$(cat "$redis_state_file" 2>/dev/null || true)
        if [[ "$redis_state" == "FAIL" && "$prev_redis" != "FAIL" ]]; then
            logger -p user.alert "osint-health-monitor: Redis ping FAIL"
            "$DIR/scripts/dr_alert_email.py" --dir "$DIR" \
                --subject "OSINT Redis ping FAIL" \
                --message "Redis op joost.iveras.com reageert niet op ping (Auth uit /etc/redis/redis-pass). Health-monitor 5-min-tick." \
                --to "$REDIS_ALERT_TO" >/dev/null 2>&1 || true
        fi
        printf '%s\n' "$redis_state" > "$redis_state_file"
    fi

    printf '%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$stamp" "$oserror" "$health" "$foreign" "$total" "$active" "$restart" "$redis_state" >> "$OUT"
    printf '%s\n' "$active" > "$active_file"
    sleep "$INTERVAL"
done
