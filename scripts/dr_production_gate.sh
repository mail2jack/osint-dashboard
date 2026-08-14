#!/usr/bin/env bash
# Read-only production before/after snapshot and independent attestation gate.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE_DIR="${DR_DRILL_EVIDENCE_DIR:-$SCRIPT_DIR/reports/dr-drill}"
PYTHON="${DR_PYTHON:-$SCRIPT_DIR/venv/bin/python3}"
if [ ! -x "$PYTHON" ]; then
    PYTHON=python3
fi

usage() {
    cat <<'EOF'
Usage:
  dr_production_gate.sh before --operator NAME
  dr_production_gate.sh after --operator NAME
  dr_production_gate.sh attest --second-operator NAME --confirm PRODUCTION-UNCHANGED
EOF
}

PHASE="${1:-}"
shift || true
OPERATOR=""
SECOND_OPERATOR=""
CONFIRMATION=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --operator) OPERATOR="$2"; shift 2 ;;
        --second-operator) SECOND_OPERATOR="$2"; shift 2 ;;
        --confirm) CONFIRMATION="$2"; shift 2 ;;
        --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

mkdir -p "$EVIDENCE_DIR"
case "$PHASE" in
    before|after)
        [ -n "$OPERATOR" ] || { printf 'Operator is required.\n' >&2; exit 2; }
        "$PYTHON" "$SCRIPT_DIR/scripts/dr_production_snapshot.py" \
            --phase "$PHASE" \
            --output "$EVIDENCE_DIR/production-$PHASE.json"
        printf 'Production %s snapshot written to %s\n' "$PHASE" "$EVIDENCE_DIR"
        ;;
    attest)
        [ -n "$SECOND_OPERATOR" ] || { printf 'Second operator is required.\n' >&2; exit 2; }
        [ "$CONFIRMATION" = "PRODUCTION-UNCHANGED" ] || {
            printf 'The second operator must confirm PRODUCTION-UNCHANGED.\n' >&2
            exit 2
        }
        BEFORE="$EVIDENCE_DIR/production-before.json"
        AFTER="$EVIDENCE_DIR/production-after.json"
        [ -f "$BEFORE" ] && [ -f "$AFTER" ] || {
            printf 'Both before and after snapshots are required.\n' >&2
            exit 2
        }
        python3 - "$BEFORE" "$AFTER" "$SECOND_OPERATOR" "$EVIDENCE_DIR" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

before_path, after_path, operator, evidence_dir = sys.argv[1:]
before = json.loads(Path(before_path).read_text(encoding="utf-8"))
after = json.loads(Path(after_path).read_text(encoding="utf-8"))
before_db = before["database"]
after_db = after["database"]
checks = {
    "same_database": before_db["name"] == after_db["name"],
    "same_schema": before_db["schema_digest"] == after_db["schema_digest"],
    "same_counts": all(
        before_db[key] == after_db[key] for key in ("tenants", "cases", "users")
    ),
    "same_uploads": before["uploads"] == after["uploads"],
    "services_recovered": all(value == "active" for value in after["services"].values()),
    "no_temporary_database": not after_db["temporary_databases"],
}
report = {
    "schema_version": 1,
    "type": "production-unchanged-attestation",
    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    "second_operator": operator,
    "checks": checks,
    "status": "pass" if all(checks.values()) else "fail",
    "before_snapshot": str(Path(before_path)),
    "after_snapshot": str(Path(after_path)),
}
output = Path(evidence_dir) / "production-unchanged-attestation.json"
output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(output)
raise SystemExit(0 if report["status"] == "pass" else 2)
PY
        ;;
    *) usage >&2; exit 2 ;;
esac
