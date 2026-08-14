#!/usr/bin/env bash
# Controlled disaster-recovery drill. This is not a production restore command.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_INPUT="${DR_DRILL_BACKUP:-${BACKUP_DIR:-$SCRIPT_DIR/backups}}"
EVIDENCE_DIR="${DR_DRILL_EVIDENCE_DIR:-$SCRIPT_DIR/reports/dr-drill}"
OPERATOR="${DR_DRILL_OPERATOR:-}"
SECOND_OPERATOR="${DR_DRILL_SECOND_OPERATOR:-}"
PRODUCTION_CONFIRMATION=""
CONFIRM=false
DRY_RUN=false

usage() {
    cat <<'EOF'
Usage: dr_drill.sh [options]

  --dry-run                         Validate configuration only.
  --confirm                         Authorize the real isolated restore drill.
  --backup PATH                     Backup archive or backup directory.
  --operator NAME                   Primary operator name.
  --second-operator NAME            Independent production-checker name.
  --production-unchanged CONFIRM    Exact value: PRODUCTION-UNCHANGED.
  --evidence-dir PATH               Audit evidence directory.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --confirm) CONFIRM=true; shift ;;
        --backup) BACKUP_INPUT="$2"; shift 2 ;;
        --operator) OPERATOR="$2"; shift 2 ;;
        --second-operator) SECOND_OPERATOR="$2"; shift 2 ;;
        --production-unchanged) PRODUCTION_CONFIRMATION="$2"; shift 2 ;;
        --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$OPERATOR" ] || [ -z "$SECOND_OPERATOR" ]; then
    printf 'Both operator identities are required.\n' >&2
    exit 2
fi
if [ "$OPERATOR" = "$SECOND_OPERATOR" ]; then
    printf 'The second operator must be independent.\n' >&2
    exit 2
fi
if [ "$DRY_RUN" = false ] && [ "$CONFIRM" != true ]; then
    printf 'Real drills require --confirm; use --dry-run for configuration checks.\n' >&2
    exit 2
fi
if [ -n "${DATABASE_URL:-}" ] && [ -n "${DR_VERIFY_DATABASE_URL:-}" ] \
    && [ "$DATABASE_URL" = "$DR_VERIFY_DATABASE_URL" ]; then
    printf 'DR database credentials must not equal production DATABASE_URL.\n' >&2
    exit 2
fi
if [ -z "${DR_VERIFY_DATABASE_URL:-}" ] && [ -z "${PGSERVICE:-}" ] && [ -z "${PGHOST:-}" ]; then
    printf 'Configure DR_VERIFY_DATABASE_URL or libpq connection settings.\n' >&2
    exit 2
fi
if [ ! -e "$BACKUP_INPUT" ]; then
    printf 'Backup path does not exist: %s\n' "$BACKUP_INPUT" >&2
    exit 2
fi

if [ "$DRY_RUN" = true ]; then
    printf 'DR dry-run passed: backup path and isolated connection settings are present.\n'
    exit 0
fi
if [ "$PRODUCTION_CONFIRMATION" != "PRODUCTION-UNCHANGED" ]; then
    printf 'The second operator must confirm production is unchanged.\n' >&2
    exit 2
fi
if [[ "$BACKUP_INPUT" != *.gpg && ! -d "$BACKUP_INPUT" ]]; then
    printf 'Use an encrypted backup archive or a directory containing one.\n' >&2
    exit 2
fi

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$EVIDENCE_DIR"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/iveras-dr-drill.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

# Prove that a wrong key fails before attempting the real recovery.
WRONG_KEY_STATUS="fail"
DRILL_ARCHIVE="$BACKUP_INPUT"
if [ -d "$BACKUP_INPUT" ]; then
    DRILL_ARCHIVE=""
    while IFS= read -r candidate; do
        DRILL_ARCHIVE="$candidate"
    done < <(find "$BACKUP_INPUT" -maxdepth 1 -type f -name 'iveras_backup_*.tar.gz.gpg' -print | sort)
fi
if [ -f "$DRILL_ARCHIVE" ] && [[ "$DRILL_ARCHIVE" == *.gpg ]]; then
    WRONG_KEY="$WORK_DIR/wrong-backup-key"
    openssl rand -base64 32 > "$WRONG_KEY"
    set +e
    DR_BACKUP_KEY_FILE="$WRONG_KEY" DR_REPORT_DIR="$WORK_DIR/wrong-key-report" \
        "$SCRIPT_DIR/scripts/verify_backup.sh" "$DRILL_ARCHIVE" \
        >/dev/null 2>&1
    WRONG_KEY_RC=$?
    set -e
    if [ "$WRONG_KEY_RC" -ne 0 ]; then
        WRONG_KEY_STATUS="pass"
    fi
else
    printf 'The wrong-key test requires an encrypted .gpg archive.\n' >&2
    exit 2
fi

if [ "$WRONG_KEY_STATUS" != pass ]; then
    printf 'A deliberately wrong backup key was accepted.\n' >&2
    exit 2
fi

REAL_REPORT_DIR="$EVIDENCE_DIR/reports"
mkdir -p "$REAL_REPORT_DIR"
DR_REPORT_DIR="$REAL_REPORT_DIR" \
    "$SCRIPT_DIR/scripts/verify_backup.sh" "$BACKUP_INPUT" > "$WORK_DIR/verify.log"
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REPORT_PATH=""
while IFS= read -r candidate; do
    REPORT_PATH="$candidate"
done < <(find "$REAL_REPORT_DIR" -maxdepth 1 -type f -name 'dr-verification-*.json' -print | sort)
if [ -z "$REPORT_PATH" ]; then
    printf 'The recovery verifier did not produce a JSON report.\n' >&2
    exit 2
fi

DRILL_REPORT="$EVIDENCE_DIR/dr-drill-$STARTED_AT.json"
DRILL_REPORT="$DRILL_REPORT" REPORT_PATH="$REPORT_PATH" OPERATOR="$OPERATOR" \
SECOND_OPERATOR="$SECOND_OPERATOR" WRONG_KEY_STATUS="$WRONG_KEY_STATUS" \
STARTED_AT="$STARTED_AT" FINISHED_AT="$FINISHED_AT" \
COMMIT_SHA="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || printf unknown)" \
python3 - <<'PY'
import json
import os
from pathlib import Path

report = {
    "schema_version": 1,
    "type": "dr-drill",
    "started_at": os.environ["STARTED_AT"],
    "finished_at": os.environ["FINISHED_AT"],
    "commit_sha": os.environ["COMMIT_SHA"],
    "operator": os.environ["OPERATOR"],
    "second_operator": os.environ["SECOND_OPERATOR"],
    "wrong_key_test": os.environ["WRONG_KEY_STATUS"],
    "restore_report": os.environ["REPORT_PATH"],
    "status": "pass",
}
Path(os.environ["DRILL_REPORT"]).write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

printf 'DR drill passed. Evidence: %s\n' "$EVIDENCE_DIR"
