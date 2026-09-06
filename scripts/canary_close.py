#!/usr/bin/env python3
"""Finalize the Gunicorn 2-worker concurrency canary window.

Run as a one-shot by osint-canary-close.service at 16:35:44 UTC on the day
after the canary opened (window = 24h uninterrupted, no restarts, no CSV
monitoring gaps).  Recomputes the pass/fail decision from live evidence and
writes a final report to reports/rollout/ plus a journal alert.

Channel (per operator): local report file + journal only; no outbound message.

Mirrors osint-health-refresh: absolute ExecStart so repo root is on sys.path.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="[canary_close] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"
ROLLOUT_DIR = REPORTS_DIR / "rollout"
MONITORING_DIR = REPORTS_DIR / "monitoring"
HEALTH_LIGHT_CSV = MONITORING_DIR / "health-light.csv"

LOCK_PATH = "/tmp/osint-canary-close.lock"

# Canary window open as observed from ActiveEnterTimestamp. Override per
# window via the CANARY_WINDOW_OPEN_ISO env var (see install_canary_close.sh);
# the value below is the last completed window and is only a safe fallback.
WINDOW_OPEN_ISO = os.environ.get("CANARY_WINDOW_OPEN_ISO", "2026-09-04T16:43:30Z")
WINDOW_DURATION = _dt.timedelta(hours=24)

# Fields in health-light.csv.
COL_TS = "ts"
COL_OSERROR = "oserror_delta_5m"
COL_LATENCY = "health_quick_s"
COL_FOREIGN = "store_foreign"
COL_RESTART = "restart_since_last"


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def _systemd_active_enter() -> str | None:
    out = _run(["systemctl", "show", "osint-dashboard", "-p", "ActiveEnterTimestamp"])
    return out.split("=", 1)[1].strip() if "=" in out else None


def _systemd_nrestarts() -> int:
    out = _run(["systemctl", "show", "osint-dashboard", "-p", "NRestarts"])
    try:
        return int(out.split("=", 1)[1])
    except (IndexError, ValueError):
        return -1


def _systemd_active() -> str:
    return _run(["systemctl", "is-active", "osint-dashboard"])


def _read_health_light() -> list[dict[str, str]]:
    if not HEALTH_LIGHT_CSV.exists():
        return []
    rows: list[dict[str, str]] = []
    with HEALTH_LIGHT_CSV.open() as fh:
        header = [h.strip() for h in fh.readline().rstrip("\n").split(",")]
        for line in fh:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split(",")
            rows.append(dict(zip(header, fields)))
    return rows


def _parse_ts(value: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _oserror_in_journal(start: _dt.datetime, end: _dt.datetime) -> int:
    """Count journald OSError log lines with timestamp in [start, end).

    The CSV oserror_delta_5m bucket (~5 min) is attributed to the row at the
    bucket END, so near the window-open boundary it can include OSErrors that
    happened BEFORE the deploy restart completed (old-worker teardown). Counting
    the raw journald timestamps that fall strictly inside [window_open, close)
    removes that border artefact.
    """
    def _fmt(dt: _dt.datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    out = _run(
        [
            "journalctl",
            "--since",
            _fmt(start),
            "--until",
            _fmt(end),
            "--no-pager",
            "-o",
            "short-iso",
        ]
    )
    return sum(1 for line in out.splitlines() if "OSError" in line)


def analyze() -> dict:
    open_dt = _dt.datetime.fromisoformat(WINDOW_OPEN_ISO.replace("Z", "+00:00"))
    close_dt = open_dt + WINDOW_DURATION
    now = _dt.datetime.now(_dt.timezone.utc)
    is_final = now >= close_dt

    active = _systemd_active()
    nrestarts = _systemd_nrestarts()
    active_enter = _systemd_active_enter()

    rows = _read_health_light()
    win = [
        r for r in rows if (t := _parse_ts(r[COL_TS])) is not None and open_dt <= t < close_dt
    ]

    # OSError rows are summed AND bucketed by distinct 5-min sample so an
    # isolated self-healed burst is distinguishable from a sustained storm/wedge.
    # Each candidate row is cross-checked against journald: only OSErrors whose
    # raw timestamp lies inside [open_dt, close_dt) count as failures. A bucket
    # straddling window-open (deploy restart teardown) no longer false-fails.
    bucket = _dt.timedelta(minutes=5)
    oserr_in_window = 0
    oserr_bursts: list[tuple[str, int]] = []
    oserr_excluded_bursts: list[tuple[str, int]] = []
    for r in win:
        if int(r.get(COL_OSERROR, "0") or 0) <= 0:
            continue
        row_ts = _parse_ts(r[COL_TS])
        if row_ts is None:
            continue
        lo = max(row_ts - bucket, open_dt)
        hi = min(row_ts, close_dt)
        in_win = _oserror_in_journal(lo, hi)
        if in_win > 0:
            oserr_in_window += 1
            oserr_bursts.append((r[COL_TS], in_win))
        else:
            oserr_excluded_bursts.append((r[COL_TS], int(r[COL_OSERROR])))
    foreign = sum(1 for r in win if int(r.get(COL_FOREIGN, "0") or 0) > 0)
    lat = [float(r[COL_LATENCY]) for r in win if r.get(COL_LATENCY)]
    slow = sum(1 for v in lat if v > 1.0)
    restarts_after_open = sum(1 for r in win if int(r.get(COL_RESTART, "0") or 0) > 0)

    gaps: list[str] = []
    ts_sorted = sorted((t for r in win if (t := _parse_ts(r[COL_TS])) is not None))
    for prev, cur in zip(ts_sorted, ts_sorted[1:]):
        if (cur - prev) > _dt.timedelta(seconds=330):
            gaps.append(f"{prev.isoformat()} -> {cur.isoformat()}")

    # The restart row at window-open is the expected canary start.
    # NRestarts since ActiveEnter must stay 0 for the whole window.
    restarts_bad = nrestarts > 0 or restarts_after_open > 1

    failures: list[str] = []
    if active != "active":
        failures.append(f"unit not active ({active})")
    if restarts_bad:
        failures.append(f"NRestarts={nrestarts}, csv-restart-rows={restarts_after_open}")
    if gaps:
        failures.append(f"CSV gaps: {gaps}")
    if slow:
        failures.append(f"{slow} slow health checks (>1s)")
    if oserr_in_window:
        bursts = "; ".join(f"{ts}={n}" for ts, n in oserr_bursts)
        failures.append(f"{oserr_in_window} within-window OSError bursts (bursts: {bursts})")
    if foreign:
        failures.append(f"{foreign} rows with store_foreign>0")

    return {
        "now": now.isoformat(),
        "window_open": open_dt.isoformat(),
        "window_close": close_dt.isoformat(),
        "is_final": is_final,
        "pct_elapsed": (now - open_dt) / WINDOW_DURATION * 100,
        "active": active,
        "nrestarts": nrestarts,
        "active_enter": active_enter,
        "samples": len(win),
        "oserr": oserr_in_window,
        "oserr_bursts": oserr_bursts,
        "oserr_excluded_bursts": oserr_excluded_bursts,
        "foreign": foreign,
        "slow": slow,
        "avg_latency": round(sum(lat) / len(lat), 4) if lat else None,
        "max_latency": round(max(lat), 4) if lat else None,
        "restarts_after_open": restarts_after_open,
        "gaps": gaps,
        "failures": failures,
        "status": "FAIL" if failures else ("PASS" if is_final else "PENDING"),
    }


def render(a: dict) -> str:
    lines = [
        "CANARY CLOSE REPORT — GUNICORN CONCURRENCY TUNING",
        f"STAMP        : {a['now']}Z",
        f"WINDOW        : {a['window_open']}Z -> {a['window_close']}Z",
        f"FINAL         : {a['is_final']} ({a['pct_elapsed']:.1f}% elapsed)",
        f"STATUS        : {a['status']}",
        "",
        f"systemd active       : {a['active']}",
        f"systemd NRestarts    : {a['nrestarts']}",
        f"ActiveEnterTimestamp : {a['active_enter']}",
        f"samples (in window)  : {a['samples']}",
        f"oserror_delta_5m>0   : {a['oserr']}" + (f" (bursts: {a['oserr_bursts']})" if a["oserr_bursts"] else ""),
        f"store_foreign>0      : {a['foreign']}",
        f"slow health (>1s)    : {a['slow']}",
        f"avg health_quick_s   : {a['avg_latency']}",
        f"max health_quick_s   : {a['max_latency']}",
        f"restart rows post-open: {a['restarts_after_open']}",
        f"CSV gaps             : {a['gaps'] if a['gaps'] else 'none'}",
        f"oserr excluded (pre-window): {a['oserr_excluded_bursts']}",
    ]
    if a["failures"]:
        lines.append("")
        lines.append("FAILURES:")
        lines += [f"  - {f}" for f in a["failures"]]
    return "\n".join(lines)


def write_report(text: str) -> Path:
    ROLLOUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ROLLOUT_DIR / f"canary-close-gunicorn-{ts}.txt"
    path.write_text(text + "\n")
    return path


def journal(msg: str) -> None:
    subprocess.run(["logger", "-t", "osint-canary-close", "--", msg])


def main() -> int:
    with open(LOCK_PATH, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning("canary-close already running; flock contention")
            return 0

        a = analyze()

        # The timer is scheduled for 16:35 UTC the day after open; if it fires
        # early, still emit a report marked PENDING (never silently skip).
        text = render(a)
        path = write_report(text)
        logger.info("wrote %s (status=%s)", path, a["status"])

        if a["is_final"]:
            journal(f"osint-canary-close FINAL {a['status']} report={path} failures={a['failures'] or 'none'}")
        else:
            journal(f"osint-canary-close early-run {a['status']} report={path} ({a['pct_elapsed']:.1f}% elapsed)")
        return 0


if __name__ == "__main__":
    sys.exit(main())