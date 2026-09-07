#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Post-Reboot Verification (one-shot)
# ============================================================
# Runs automatically after boot (osint-reboot-verify.service, enabled by
# scripts/install_reboot_maintenance.sh). Does nothing unless the reboot
# maintenance left the marker .reboot_maintenance_armed. Writes a report to
# /var/log/osint-reboot-verify.log and clears the marker (so a later boot
# silently skips).
#
# Usage:
#   sudo ./scripts/reboot_verify.sh                # run (usually post-boot)
#   sudo ./scripts/reboot_verify.sh --force        # run even without marker
#
set -uo pipefail

DIR="/opt/osint-dashboard"
LOG="/var/log/osint-reboot-verify.log"
MARKER="$DIR/.reboot_maintenance_armed"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: draai als root (sudo ./scripts/reboot_verify.sh)" >&2
    exit 1
fi

if [ "${1:-}" != "--force" ] && [ ! -f "$MARKER" ]; then
    logger -t osint-reboot-verify "marker ontbreekt — niets te verifiëren"
    exit 0
fi

exec > >(tee --append "$LOG" >(logger -t osint-reboot-verify -p user.info) >/dev/null) 2>&1
echo "==== reboot_verify start $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="

echo "=== basis ==="
uname -a
uptime

echo "=== wachten op health (max 180s) ==="
HEALTH=0
for i in $(seq 1 60); do
    if curl -fsS http://localhost:5000/api/v1/health >/dev/null 2>&1; then
        HEALTH=1
        break
    fi
    sleep 3
done
if [ "$HEALTH" -eq 1 ]; then
    echo "  OK: /api/v1/health bereikbaar."
else
    echo "  FAIL: /api/v1/health niet bereikbaar binnen 180s."
fi

echo "=== /health (full readiness, HTTP-code) ==="
curl -sS -o /dev/null -w "  /health -> %{http_code}\n" http://localhost:5000/health \
    || echo "  FAIL: /health niet bereikbaar."
curl -sS http://localhost:5000/health 2>/dev/null | head -c 300
echo ""

echo "=== actieve units ==="
for u in osint-dashboard postgresql nginx spiderfoot osint-bot license-server; do
    if systemctl is-active "$u" >/dev/null 2>&1; then
        echo "  OK: $u actief."
    else
        echo "  WARN: $u niet actief."
    fi
done

echo "=== failed units ==="
systemctl list-units --failed --no-legend || true

echo "=== journald errors sinds boot ==="
journalctl -b -p err --no-pager | tail -10 || true

echo "=== RLS + alembic head ==="
DBURL=$(grep -m1 "^DATABASE_URL=" "$DIR/.env" | cut -d= -f2- || true)
if [ -n "$DBURL" ] && [ -x "$DIR/venv/bin/python3" ]; then
    sudo -u osint env DBURL="$DBURL" "$DIR/venv/bin/python3" - "$LOG" <<'PY' || echo "  WARN: DB-controle mislukt"
import os
try:
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DBURL"], connect_args={"sslmode": "require"})
    with engine.connect() as c:
        print("  alembic_head:", c.execute(text("SELECT version_num FROM alembic_version")).scalar())
        print("  force_rls_tables:", c.execute(
            text("SELECT count(DISTINCT c.oid) FROM pg_class c "
                 "JOIN pg_namespace n ON n.oid=c.relnamespace "
                 "JOIN pg_policy p ON p.polrelid=c.oid "
                 "WHERE n.nspname='public' AND c.relkind IN ('r','p')")).scalar())
        print("  background_tasks_policies:", c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename='background_tasks'")).scalar())
except Exception as e:
    print("  WARN: DB-controle mislukt:", type(e).__name__)
PY
else
    echo "  WARN: geen DATABASE_URL/venv — DB-controle overgeslagen."
fi

echo "=== disk ==="
df -h / | tail -1

rm -f "$MARKER"
echo "  marker verwijderd."
echo "==== reboot_verify VOLTOOID $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
exit 0