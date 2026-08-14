#!/usr/bin/env python3
"""Write a secret-free production rollout report."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def write_report(output: Path, commit_sha: str, checks_file: Path, status: str) -> None:
    checks = {}
    for line in checks_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            name, result, detail = line.split("\t", 2)
            checks[name] = {"status": result, "detail": detail}
    report = {
        "schema_version": 1,
        "type": "production-rollout",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "commit_sha": commit_sha,
        "status": status,
        "checks": checks,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--checks-file", type=Path, required=True)
    parser.add_argument("--status", choices=("pass", "fail"), required=True)
    args = parser.parse_args()
    write_report(args.output, args.commit_sha, args.checks_file, args.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
