#!/usr/bin/env bash
set -euo pipefail

MESSAGE="OSINT disaster-recovery verification failed on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ). See the system journal and the DR report directory."

if command -v logger >/dev/null 2>&1; then
    logger -p user.alert -t osint-backup-verify -- "$MESSAGE"
fi

if [ -n "${BACKUP_VERIFY_ALERT_EMAIL:-}" ] && command -v mail >/dev/null 2>&1; then
    printf '%s\n' "$MESSAGE" | mail -s "OSINT backup verification failed" "$BACKUP_VERIFY_ALERT_EMAIL"
fi
