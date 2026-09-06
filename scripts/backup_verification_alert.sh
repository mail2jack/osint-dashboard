#!/usr/bin/env bash
set -euo pipefail

MESSAGE="OSINT disaster-recovery verification failed on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ). See the system journal and the DR report directory."

if command -v logger >/dev/null 2>&1; then
    logger -p user.alert -t osint-backup-verify -- "$MESSAGE"
fi

if [ -n "${BACKUP_VERIFY_ALERT_EMAIL:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    PYTHON="${PYTHON:-$PROJECT_DIR/venv/bin/python}"
    if [ -x "$PYTHON" ]; then
        IFS=',' read -r -a TO_LIST <<< "$BACKUP_VERIFY_ALERT_EMAIL"
        "$PYTHON" "$SCRIPT_DIR/dr_alert_email.py" \
            --dir "$PROJECT_DIR" \
            --subject "OSINT backup verification failed" \
            --message "$MESSAGE" \
            --to "${TO_LIST[@]}" \
            || echo "[backup-verification-alert] ⚠️  dr_alert_email.py faalde" >&2
    else
        echo "[backup-verification-alert] ⚠️  geen werkende python op $PYTHON — e-mail overgeslagen" >&2
    fi
fi
