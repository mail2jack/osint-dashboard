#!/usr/bin/env python3
"""Write the machine-readable result of a disaster-recovery verification."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def _read_checks(path: Path) -> dict[str, dict[str, str]]:
    checks = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, status, detail = line.split("\t", 2)
        checks[name] = {"status": status, "detail": detail}
    return checks


def write_report(
    output: Path,
    *,
    backup_id: str,
    commit_sha: str,
    checks: dict[str, dict[str, str]],
    counts: dict[str, int | None],
) -> dict:
    status = (
        "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    )
    report = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "backup_id": backup_id,
        "commit_sha": commit_sha,
        "status": status,
        "checks": checks,
        "counts": counts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--checks-file", type=Path, required=True)
    parser.add_argument("--counts-json", default="{}")
    args = parser.parse_args()
    report = write_report(
        args.output,
        backup_id=args.backup_id,
        commit_sha=args.commit_sha,
        checks=_read_checks(args.checks_file),
        counts=json.loads(args.counts_json),
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
