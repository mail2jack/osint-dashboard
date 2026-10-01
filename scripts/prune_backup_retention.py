#!/usr/bin/env python3
"""Apply the Joost backup retention policy.

The default is a dry run.  Pass ``--apply`` to remove archives that are not
selected by any retention tier.  Archives are selected from their timestamped
filename, not filesystem mtime, so copying an archive does not change policy.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path

PATTERN = re.compile(r"^iveras_backup_(\d{8})_(\d{6})\.tar\.gz\.gpg$")


def archive_date(path: Path) -> datetime | None:
    match = PATTERN.match(path.name)
    if not match:
        return None
    return datetime.strptime("_".join(match.groups()), "%Y%m%d_%H%M%S")


def selected_archives(paths: list[Path], now: datetime, daily: int, weekly: int, monthly: int) -> set[Path]:
    dated = [(path, archive_date(path)) for path in paths]
    dated = [(path, stamp) for path, stamp in dated if stamp is not None and stamp <= now]
    keep: set[Path] = set()

    by_day: dict[tuple[int, int, int], Path] = {}
    for path, stamp in dated:
        if stamp >= now - timedelta(days=daily):
            key = (stamp.year, stamp.month, stamp.day)
            if key not in by_day or stamp > archive_date(by_day[key]):
                by_day[key] = path
    keep.update(by_day.values())

    by_week: dict[tuple[int, int], Path] = {}
    for path, stamp in dated:
        if stamp >= now - timedelta(weeks=weekly):
            key = stamp.isocalendar()[:2]
            if key not in by_week or stamp > archive_date(by_week[key]):
                by_week[key] = path
    keep.update(by_week.values())

    by_month: dict[tuple[int, int], Path] = {}
    for path, stamp in dated:
        if stamp >= now - timedelta(days=monthly * 31):
            key = (stamp.year, stamp.month)
            if key not in by_month or stamp > archive_date(by_month[key]):
                by_month[key] = path
    keep.update(by_month.values())
    return keep


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--daily", type=int, default=14)
    parser.add_argument("--weekly", type=int, default=8)
    parser.add_argument("--monthly", type=int, default=12)
    parser.add_argument("--apply", action="store_true", help="delete unselected archives")
    args = parser.parse_args()
    if min(args.daily, args.weekly, args.monthly) < 1:
        parser.error("retention values must be positive")

    paths = sorted(args.directory.glob("iveras_backup_*.tar.gz.gpg"))
    keep = selected_archives(paths, datetime.now(), args.daily, args.weekly, args.monthly)
    remove = [path for path in paths if path not in keep and archive_date(path) is not None]
    print(f"Retention policy: daily={args.daily}, weekly={args.weekly}, monthly={args.monthly}")
    print(f"Keeping {len(keep)} archive(s); removing {len(remove)} archive(s)")
    for path in remove:
        print(f"  {'REMOVE' if args.apply else 'WOULD REMOVE'} {path}")
        if args.apply:
            path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
