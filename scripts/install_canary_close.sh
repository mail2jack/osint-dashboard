#!/usr/bin/env bash
# Install/arm the canary-close and canary-check timers.
#
# RE-ARM GOTCHA (geverifieerd 2026-09-04 op prod):
#   Een eenmalige OnCalendar-timer die al een keer gevuurd heeft, herberekent
#   de next-elapse NIET bij `systemctl start` of `enable --now`. Het resultaat
#   is ActiveState=active maar NextElapseUSecRealtime= (leeg): de timer is
#   schijnbaar "active" maar vuurt nooit meer. Forceer herberekening met
#   `systemctl restart <timer>` (of `systemctl reset-failed` + `start`).
#   Verifieer daarna altijd NextElapseUSecRealtime != leeg.
#
# Gebruik:
#   1) Pas window-datum aan in deploy/osint-canary-close.timer
#      (OnCalendar = window-close + ~3 min) en de check-timer (+~3 min).
#   2) Optioneel: export CANARY_WINDOW_OPEN_ISO="YYYY-MM-DDTHH:MM:SSZ"
#      (venster-open zoals ActiveEnterTimestamp) — canary_close.py gebruikt die
#      bij zijn run; zonder override vallen we terug op de laatste window.
#   3) Draai dit script als root.
#   4) Controleer de output: beide NextElapse moeten een timestamp tonen.
set -euo pipefail

APP_DIR="/opt/osint-dashboard"
UNIT_DIR="/etc/systemd/system"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

install -o root -g root -m 0644 \
    "$APP_DIR/deploy/osint-canary-close.service" \
    "$APP_DIR/deploy/osint-canary-close.timer" \
    "$APP_DIR/deploy/osint-canary-check.service" \
    "$APP_DIR/deploy/osint-canary-check.timer" \
    "$UNIT_DIR/"
systemctl daemon-reload
# Deploy-timers hebben [Install] WantedBy=timers.target (persist over boots).
systemctl enable osint-canary-close.timer
systemctl enable osint-canary-check.timer
# RESTART (niet "start"/"enable --now") — zie de gotcha bovenaan: alleen
# restart forceert herberekening van de eenmalige OnCalendar naar next-elapse.
systemctl restart osint-canary-close.timer
systemctl restart osint-canary-check.timer

# ---- harde verificatie: NextElapse mag niet leeg zijn ----
for t in osint-canary-close.timer osint-canary-check.timer; do
    next="$(systemctl show "$t" -p NextElapseUSecRealtime --value)"
    trg="$(systemctl show "$t" -p TimersOnCalendar --value)"
    echo "$t: next=$next (oncalendar=$trg)"
    if [ -z "$next" ]; then
        echo "FATAL: $t is active maar heeft lege NextElapse — timer is NIET armed."
        echo "Herstel: systemctl restart $t en verifieer opnieuw."
        exit 1
    fi
done
echo "Canary-close/check timers armed OK."
